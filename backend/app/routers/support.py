"""Обращения в поддержку. Видят автор и admin; admin отвечает и меняет статус в админке."""

from fastapi import APIRouter, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import Session
from app.errors import not_found
from app.models import SupportMessage, SupportTicket, User
from app.pagination import Page, Pagination, paginate
from app.routers.bookings import get_booking_for
from app.schemas.social import (
    SupportMessageCreate,
    SupportMessageOut,
    TicketCreate,
    TicketDetail,
    TicketOut,
)
from app.security import CurrentUser

router = APIRouter(prefix="/support/tickets", tags=["support"])


async def _my_ticket(session: AsyncSession, me: User, ticket_id: int) -> SupportTicket:
    ticket = await session.get(SupportTicket, ticket_id)
    if ticket is None or ticket.author_id != me.id:
        raise not_found("Обращение не найдено")
    return ticket


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_ticket(data: TicketCreate, me: CurrentUser, session: Session) -> TicketDetail:
    """Обращение. Хозяин, который не может принять гостя, пишет сюда с booking_id —
    отменить такую бронь может только admin."""
    if data.booking_id is not None:
        await get_booking_for(session, me, data.booking_id)  # только своя бронь
    ticket = SupportTicket(author_id=me.id, **data.model_dump())
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket, ["messages"])
    return TicketDetail.model_validate(ticket)


@router.get("")
async def list_tickets(me: CurrentUser, session: Session, page: Pagination) -> Page[TicketOut]:
    query = (
        select(SupportTicket)
        .where(SupportTicket.author_id == me.id)
        .order_by(SupportTicket.id.desc())
    )
    return Page[TicketOut].model_validate(await paginate(session, query, page))


@router.get("/{ticket_id}")
async def get_ticket(ticket_id: int, me: CurrentUser, session: Session) -> TicketDetail:
    """Обращение с историей сообщений."""
    return TicketDetail.model_validate(await _my_ticket(session, me, ticket_id))


@router.post("/{ticket_id}/messages", status_code=status.HTTP_201_CREATED)
async def add_ticket_message(
    ticket_id: int, data: SupportMessageCreate, me: CurrentUser, session: Session
) -> SupportMessageOut:
    await _my_ticket(session, me, ticket_id)
    message = SupportMessage(ticket_id=ticket_id, author_id=me.id, text=data.text)
    session.add(message)
    await session.commit()
    await session.refresh(message)
    return SupportMessageOut.model_validate(message)
