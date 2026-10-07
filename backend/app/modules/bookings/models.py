from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    Text,
    Time,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.users.models import pg_enum


class BookingType(StrEnum):
    STAY = "stay"
    VEHICLE = "vehicle"


class BookingStatus(StrEnum):
    HOLD = "hold"
    CONFIRMED = "confirmed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED_BY_GUEST = "cancelled_by_guest"
    CANCELLED_BY_SUPPORT = "cancelled_by_support"
    EXPIRED = "expired"
    NO_SHOW = "no_show"


# Статусы, которые занимают места / даты (R-12, R-18, R-19).
ACTIVE_STATUSES = (BookingStatus.HOLD, BookingStatus.CONFIRMED, BookingStatus.IN_PROGRESS)


class Booking(Base):
    """Бронь проживания (stay) или машины (vehicle), R-17.

    EXCLUDE-ограничения bookings_guest_no_overlap (R-18) и bookings_vehicle_no_overlap (R-19)
    есть только в миграции 0010 — в модели SQLAlchemy их не описываем.
    """

    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("unit_price > 0"),
        CheckConstraint("commission_rate >= 0 AND commission_rate < 1"),
        CheckConstraint("guests_count > 0"),
        CheckConstraint("(type = 'stay') = (host_id IS NOT NULL)", name="bookings_host_chk"),
        CheckConstraint("(type = 'stay') = (guests_count IS NOT NULL)", name="bookings_guests_chk"),
        CheckConstraint(
            "(type = 'stay') = (arrival_time IS NOT NULL)", name="bookings_arrival_chk"
        ),
        CheckConstraint(
            "(type = 'vehicle') = (vehicle_id IS NOT NULL)", name="bookings_vehicle_chk"
        ),
        CheckConstraint(
            "(type = 'vehicle') = (pickup_location IS NOT NULL)", name="bookings_pickup_chk"
        ),
        CheckConstraint(
            "(type = 'vehicle') = (return_location IS NOT NULL)", name="bookings_return_chk"
        ),
        CheckConstraint("end_date > start_date", name="bookings_dates_chk"),
        CheckConstraint("guest_id <> host_id", name="bookings_not_self_chk"),
        CheckConstraint(
            "base_amount = unit_price * COALESCE(guests_count, 1) * (end_date - start_date)",
            name="bookings_base_amount_chk",
        ),
        Index(
            "bookings_host_idx",
            "host_id",
            "start_date",
            postgresql_where=text("type = 'stay'"),
        ),
        Index(
            "bookings_vehicle_idx",
            "vehicle_id",
            "start_date",
            postgresql_where=text("type = 'vehicle'"),
        ),
        Index("bookings_guest_idx", "guest_id", text("start_date DESC")),
        Index("bookings_hold_idx", "hold_expires_at", postgresql_where=text("status = 'hold'")),
        Index(
            "bookings_ack_idx",
            "start_date",
            postgresql_where=text(
                "type = 'stay' AND status = 'confirmed' AND host_acknowledged_at IS NULL"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    type: Mapped[BookingType] = mapped_column(pg_enum(BookingType, "booking_type"))
    guest_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    host_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("host_profiles.user_id"))
    vehicle_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("vehicles.id"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    base_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    commission_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    currency: Mapped[str] = mapped_column(CHAR(3), server_default="KGS")
    status: Mapped[BookingStatus] = mapped_column(
        pg_enum(BookingStatus, "booking_status"), server_default=BookingStatus.HOLD.value
    )
    hold_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    cancel_reason: Mapped[str | None] = mapped_column(Text)

    guests_count: Mapped[int | None] = mapped_column(SmallInteger)
    arrival_time: Mapped[time | None] = mapped_column(Time)
    host_acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checkin_pin_attempts: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    pickup_location: Mapped[str | None] = mapped_column(Text)
    return_location: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
