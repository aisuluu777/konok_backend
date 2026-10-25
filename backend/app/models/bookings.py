from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, str_enum

if TYPE_CHECKING:
    from app.models.activities import Activity
    from app.models.payments import Payment


class BookingType(StrEnum):
    STAY = "stay"
    VEHICLE = "vehicle"


class BookingStatus(StrEnum):
    PENDING_PAYMENT = "pending_payment"  # идёт оплата: места уже заняты
    CONFIRMED = "confirmed"  # оплачена
    IN_PROGRESS = "in_progress"  # заселение / передача авто по PIN
    COMPLETED = "completed"
    CANCELLED_BY_GUEST = "cancelled_by_guest"
    CANCELLED_BY_ADMIN = "cancelled_by_admin"
    FAILED = "failed"  # к 12:00 дня выезда заселения по PIN не было


class Booking(Base):
    """Бронь проживания (host_id) или аренды авто (vehicle_id).

    Ночи / дни аренды — [start_date, end_date): день выезда / возврата не входит.
    """

    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("end_date > start_date", name="dates_order"),
        CheckConstraint(
            "(type = 'stay' AND host_id IS NOT NULL AND vehicle_id IS NULL"
            " AND guests_count > 0)"
            " OR (type = 'vehicle' AND vehicle_id IS NOT NULL AND host_id IS NULL"
            " AND guests_count IS NULL)",
            name="type_target",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    type: Mapped[BookingType] = mapped_column(str_enum(BookingType))
    guest_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    host_id: Mapped[int | None] = mapped_column(ForeignKey("host_profiles.user_id"), index=True)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    guests_count: Mapped[int | None]

    # Снимки на момент создания: цена за человека-ночь / за день, сумма, ставка комиссии.
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    commission_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4))

    status: Mapped[BookingStatus] = mapped_column(
        str_enum(BookingStatus), default=BookingStatus.PENDING_PAYMENT, index=True
    )
    # «Вижу, жду гостя» — отметка хозяина, не статус.
    host_acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # Экстренная отмена admin: уважительная ли причина (false → скрытая оценка 1 хозяину).
    admin_reason_valid: Mapped[bool | None]

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    payments: Mapped[list["Payment"]] = relationship(lazy="selectin", order_by="Payment.id")
    activities: Mapped[list["BookingActivity"]] = relationship(
        lazy="selectin", order_by="BookingActivity.id"
    )

    def __str__(self) -> str:
        return f"Бронь #{self.id}"


class BookingActivity(Base):
    """Активность, купленная к брони проживания; цена — снимок на момент покупки."""

    __tablename__ = "booking_activities"
    __table_args__ = (CheckConstraint("persons > 0", name="persons_positive"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), index=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id"))
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"), index=True)
    persons: Mapped[int]
    price_per_person: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))

    activity: Mapped["Activity"] = relationship(lazy="selectin")
