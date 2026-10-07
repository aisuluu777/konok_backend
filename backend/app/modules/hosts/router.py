from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BigId, SmallId, get_session
from app.core.security import CurrentUser, get_current_user
from app.modules.hosts import service
from app.modules.hosts.schemas import (
    BlockedDatesIn,
    BlockedDatesOut,
    CapacityIn,
    HostOwnerOut,
    HostProfileCreate,
    HostPublicOut,
)

Session = Annotated[AsyncSession, Depends(get_session)]
Me = Annotated[CurrentUser, Depends(get_current_user)]

router = APIRouter(prefix="/hosts", tags=["hosts"])


@router.post("/me", status_code=status.HTTP_201_CREATED)
async def create_my_profile(data: HostProfileCreate, me: Me, session: Session) -> HostOwnerOut:
    return HostOwnerOut.model_validate(await service.create_profile(session, me.id, data))


@router.get("/me")
async def get_my_profile(me: Me, session: Session) -> HostOwnerOut:
    return HostOwnerOut.model_validate(await service.get_own_profile(session, me.id))


@router.get("/me/blocked-dates")
async def get_my_blocked_dates(me: Me, session: Session) -> BlockedDatesOut:
    return BlockedDatesOut.model_validate(await service.get_own_profile(session, me.id))


@router.put("/me/blocked-dates")
async def set_my_blocked_dates(data: BlockedDatesIn, me: Me, session: Session) -> BlockedDatesOut:
    profile = await service.set_blocked_dates(session, me.id, data.dates)
    return BlockedDatesOut.model_validate(profile)


@router.put("/me/capacity")
async def set_my_capacity(data: CapacityIn, me: Me, session: Session) -> HostOwnerOut:
    return HostOwnerOut.model_validate(await service.set_capacity(session, me.id, data.capacity))


@router.get("")
async def list_catalog(
    session: Session,
    region_id: SmallId | None = None,
    tag_ids: Annotated[list[SmallId] | None, Query(max_length=20)] = None,
) -> list[HostPublicOut]:
    hosts = await service.list_catalog(session, region_id, tag_ids or [])
    return [HostPublicOut.model_validate(h) for h in hosts]


@router.get("/{host_id}")
async def get_host(host_id: BigId, session: Session) -> HostPublicOut:
    return HostPublicOut.model_validate(await service.get_public_profile(session, host_id))
