from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BigId, get_session
from app.core.security import CurrentUser, get_current_user
from app.modules.activities import service
from app.modules.activities.schemas import (
    ActivityCreate,
    ActivityOut,
    ActivityOwnerOut,
    ActivityUpdate,
)

Session = Annotated[AsyncSession, Depends(get_session)]
Me = Annotated[CurrentUser, Depends(get_current_user)]

router = APIRouter(tags=["activities"])


@router.post("/activities", status_code=status.HTTP_201_CREATED)
async def create_activity(data: ActivityCreate, me: Me, session: Session) -> ActivityOwnerOut:
    return ActivityOwnerOut.model_validate(await service.create_activity(session, me.id, data))


@router.get("/activities/my")
async def list_my_activities(me: Me, session: Session) -> list[ActivityOwnerOut]:
    activities = await service.list_own_activities(session, me.id)
    return [ActivityOwnerOut.model_validate(a) for a in activities]


@router.patch("/activities/{activity_id}")
async def update_activity(
    activity_id: BigId, data: ActivityUpdate, me: Me, session: Session
) -> ActivityOwnerOut:
    activity = await service.update_activity(session, me.id, activity_id, data)
    return ActivityOwnerOut.model_validate(activity)


@router.post("/activities/{activity_id}/deactivate")
async def deactivate_activity(activity_id: BigId, me: Me, session: Session) -> ActivityOwnerOut:
    activity = await service.deactivate_activity(session, me.id, activity_id)
    return ActivityOwnerOut.model_validate(activity)


@router.get("/hosts/{host_id}/activities")
async def list_host_activities(host_id: BigId, session: Session) -> list[ActivityOut]:
    activities = await service.list_host_activities(session, host_id)
    return [ActivityOut.model_validate(a) for a in activities]
