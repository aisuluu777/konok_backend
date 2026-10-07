from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BigId, get_session
from app.core.security import CurrentUser, get_current_user
from app.modules.notifications import service
from app.modules.notifications.schemas import NotificationOut, ReadAllOut

Session = Annotated[AsyncSession, Depends(get_session)]
Me = Annotated[CurrentUser, Depends(get_current_user)]

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(me: Me, session: Session) -> list[NotificationOut]:
    """Только свои in-app уведомления; чтение не меняет read_at (R-39)."""
    return [
        NotificationOut.model_validate(n) for n in await service.list_notifications(session, me.id)
    ]


@router.post("/read-all")
async def mark_all_read(me: Me, session: Session) -> ReadAllOut:
    return ReadAllOut(updated=await service.mark_all_read(session, me.id))


@router.post("/{notification_id}/read")
async def mark_read(notification_id: BigId, me: Me, session: Session) -> NotificationOut:
    return NotificationOut.model_validate(await service.mark_read(session, me.id, notification_id))
