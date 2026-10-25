from fastapi import APIRouter, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage
from app.deps import Session
from app.errors import bad_request, forbidden, not_found
from app.models import Activity, ActivityPhoto, HostProfile, Tag, User
from app.pagination import Page, Pagination, paginate
from app.schemas.activities import ActivityCreate, ActivityOut, ActivityUpdate
from app.security import CurrentUser

router = APIRouter(prefix="/activities", tags=["activities"])


async def _tags(session: AsyncSession, tag_ids: list[int]) -> list[Tag]:
    tags = list(await session.scalars(select(Tag).where(Tag.id.in_(set(tag_ids)))))
    if len(tags) != len(set(tag_ids)):
        raise bad_request("Тег не найден")
    return tags


async def _my_activity(session: AsyncSession, me: User, activity_id: int) -> Activity:
    activity = await session.get(Activity, activity_id)
    if activity is None or activity.host_id != me.id:
        raise not_found("Активность не найдена")
    return activity


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_activity(data: ActivityCreate, me: CurrentUser, session: Session) -> ActivityOut:
    """Создать активность может только хозяин. Цена за человека обязательна."""
    if await session.get(HostProfile, me.id) is None:
        raise forbidden("Сначала создайте профиль хозяина")
    activity = Activity(
        host_id=me.id,
        **data.model_dump(exclude={"tag_ids"}),
        tags=await _tags(session, data.tag_ids),
    )
    session.add(activity)
    await session.commit()
    await session.refresh(activity, ["tags", "photos"])
    return ActivityOut.model_validate(activity)


@router.get("/my")
async def list_my_activities(
    me: CurrentUser, session: Session, page: Pagination
) -> Page[ActivityOut]:
    query = select(Activity).where(Activity.host_id == me.id).order_by(Activity.id)
    return Page[ActivityOut].model_validate(await paginate(session, query, page))


@router.patch("/{activity_id}")
async def update_activity(
    activity_id: int, data: ActivityUpdate, me: CurrentUser, session: Session
) -> ActivityOut:
    activity = await _my_activity(session, me, activity_id)
    changes = data.model_dump(exclude_unset=True)
    tag_ids = changes.pop("tag_ids", None)
    if tag_ids is not None:
        activity.tags = await _tags(session, tag_ids)
    for field, value in changes.items():
        if value is None and field in {"price_per_person", "is_active"}:
            continue
        setattr(activity, field, value)
    if not (activity.title_ky or activity.title_ru or activity.title_en):
        raise bad_request("Укажите название хотя бы на одном языке")
    await session.commit()
    return ActivityOut.model_validate(activity)


@router.post("/{activity_id}/photos", status_code=status.HTTP_201_CREATED)
async def add_activity_photo(
    activity_id: int, file: UploadFile, me: CurrentUser, session: Session
) -> ActivityOut:
    activity = await _my_activity(session, me, activity_id)
    activity.photos.append(ActivityPhoto(url=await storage.save_photo(file)))
    await session.commit()
    return ActivityOut.model_validate(activity)


@router.delete("/{activity_id}/photos/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_activity_photo(
    activity_id: int, photo_id: int, me: CurrentUser, session: Session
) -> None:
    await _my_activity(session, me, activity_id)
    photo = await session.get(ActivityPhoto, photo_id)
    if photo is None or photo.activity_id != activity_id:
        raise not_found("Фото не найдено")
    storage.delete_photo(photo.url)
    await session.delete(photo)
    await session.commit()
