from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.users.models import pg_enum


class TicketStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    WAITING_REPLY = "waiting_reply"
    RESOLVED = "resolved"
    CLOSED = "closed"


class SupportTicket(Base):
    """Обращение в поддержку; жалоба — category = 'complaint' со свободным текстом (R-27)."""

    __tablename__ = "support_tickets"
    __table_args__ = (Index("support_tickets_queue_idx", "status", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    booking_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("bookings.id"))
    category: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TicketStatus] = mapped_column(
        pg_enum(TicketStatus, "ticket_status"), server_default=TicketStatus.NEW.value
    )
    assigned_to: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
