from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import CurrentUser, get_current_user
from app.modules.guests import service
from app.modules.guests.schemas import GuestProfileIn, GuestProfileOut

Session = Annotated[AsyncSession, Depends(get_session)]
Me = Annotated[CurrentUser, Depends(get_current_user)]

# Только свой профиль. Чужой (возраст без даты) хозяин увидит в контексте брони — Step 10.
router = APIRouter(prefix="/guests", tags=["guests"])


@router.get("/me")
async def get_my_profile(me: Me, session: Session) -> GuestProfileOut:
    return GuestProfileOut.from_profile(await service.get_profile(session, me.id))


@router.put("/me")
async def put_my_profile(data: GuestProfileIn, me: Me, session: Session) -> GuestProfileOut:
    return GuestProfileOut.from_profile(await service.put_profile(session, me.id, data))
