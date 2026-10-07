from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.security import CurrentUser
from app.modules.bookings.models import Booking
from app.modules.payments.service import provider_id
from app.modules.support.models import SupportTicket, TicketStatus
from app.modules.support.schemas import TicketCreate
from app.modules.users.models import UserRole

STAFF = (UserRole.MODERATOR, UserRole.ADMIN)


async def create_ticket(session: AsyncSession, user_id: int, data: TicketCreate) -> SupportTicket:
    """Обращение или жалоба (R-27); хозяин и владелец машины пишут так же, как гость (R-22).

    К брони можно привязать только свою — как гостя или как хозяина / владельца машины.
    """
    if data.booking_id is not None:
        booking = await session.get(Booking, data.booking_id)
        if booking is None or user_id not in (
            booking.guest_id,
            await provider_id(session, booking),
        ):
            raise NotFoundError("Booking not found")
    ticket = SupportTicket(
        user_id=user_id,
        booking_id=data.booking_id,
        category=data.category,
        description=data.description,
    )
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)
    return ticket


async def my_tickets(session: AsyncSession, user_id: int) -> Sequence[SupportTicket]:
    rows = await session.scalars(
        select(SupportTicket)
        .where(SupportTicket.user_id == user_id)
        .order_by(SupportTicket.created_at.desc(), SupportTicket.id.desc())
    )
    return rows.all()


async def queue(session: AsyncSession, status: TicketStatus | None) -> Sequence[SupportTicket]:
    """Очередь модератора: старые первыми, можно отфильтровать по статусу."""
    query = select(SupportTicket).order_by(SupportTicket.created_at, SupportTicket.id)
    if status is not None:
        query = query.where(SupportTicket.status == status)
    return (await session.scalars(query)).all()


async def get_ticket(session: AsyncSession, viewer: CurrentUser, ticket_id: int) -> SupportTicket:
    """Тикет видят автор и сотрудники; чужой — 404."""
    ticket = await session.get(SupportTicket, ticket_id)
    if ticket is None or (viewer.role not in STAFF and ticket.user_id != viewer.id):
        raise NotFoundError("Ticket not found")
    return ticket


async def set_status(
    session: AsyncSession, staff_id: int, ticket_id: int, status: TicketStatus
) -> SupportTicket:
    """Смена статуса сотрудником; тикет без исполнителя закрепляется за ним."""
    ticket = await session.scalar(
        select(SupportTicket).where(SupportTicket.id == ticket_id).with_for_update()
    )
    if ticket is None:
        raise NotFoundError("Ticket not found")
    ticket.status = status
    if ticket.assigned_to is None:
        ticket.assigned_to = staff_id
    await session.commit()
    await session.refresh(ticket)
    return ticket
