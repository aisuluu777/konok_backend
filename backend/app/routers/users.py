from fastapi import APIRouter, UploadFile

from app import storage
from app.deps import Session
from app.models import User
from app.schemas.users import UserMe, UserUpdate
from app.security import CurrentUser

router = APIRouter(prefix="/users", tags=["users"])


def user_me(user: User) -> UserMe:
    return UserMe.model_validate(
        {
            **{f: getattr(user, f) for f in UserMe.model_fields if hasattr(user, f)},
            "has_id_document": user.id_document_url is not None,
            "has_driver_license": user.driver_license_url is not None,
        }
    )


@router.get("/me")
async def get_me(me: CurrentUser) -> UserMe:
    """Профиль, статус проверки документов и PIN заселения."""
    return user_me(me)


@router.patch("/me")
async def update_me(data: UserUpdate, me: CurrentUser, session: Session) -> UserMe:
    me.full_name = data.full_name
    await session.commit()
    return user_me(me)


@router.post("/me/id-document")
async def upload_id_document(file: UploadFile, me: CurrentUser, session: Session) -> UserMe:
    """Документ (ID) для KYC: JPEG / PNG / WebP / PDF до 10 МБ. Проверяет admin."""
    me.id_document_url = await storage.save_document(file)
    await session.commit()
    return user_me(me)


@router.post("/me/driver-license")
async def upload_driver_license(file: UploadFile, me: CurrentUser, session: Session) -> UserMe:
    """Водительское удостоверение для аренды авто. Проверяет admin."""
    me.driver_license_url = await storage.save_document(file)
    await session.commit()
    return user_me(me)
