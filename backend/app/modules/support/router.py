from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BigId, get_session
from app.core.security import CurrentUser, get_current_user, require_role
from app.modules.support import service
from app.modules.support.models import TicketStatus
from app.modules.support.schemas import StatusIn, TicketCreate, TicketOut

Session = Annotated[AsyncSession, Depends(get_session)]
Me = Annotated[CurrentUser, Depends(get_current_user)]
Staff = Annotated[CurrentUser, Depends(require_role(*service.STAFF))]

router = APIRouter(prefix="/support/tickets", tags=["support"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_ticket(data: TicketCreate, me: Me, session: Session) -> TicketOut:
    return TicketOut.model_validate(await service.create_ticket(session, me.id, data))


@router.get("/my")
async def my_tickets(me: Me, session: Session) -> list[TicketOut]:
    return [TicketOut.model_validate(t) for t in await service.my_tickets(session, me.id)]


@router.get("")
async def queue(_: Staff, session: Session, status: TicketStatus | None = None) -> list[TicketOut]:
    return [TicketOut.model_validate(t) for t in await service.queue(session, status)]


@router.get("/{ticket_id}")
async def get_ticket(ticket_id: BigId, me: Me, session: Session) -> TicketOut:
    return TicketOut.model_validate(await service.get_ticket(session, me, ticket_id))


@router.post("/{ticket_id}/status")
async def set_status(ticket_id: BigId, data: StatusIn, staff: Staff, session: Session) -> TicketOut:
    ticket = await service.set_status(session, staff.id, ticket_id, data.status)
    return TicketOut.model_validate(ticket)
