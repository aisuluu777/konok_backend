from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.users.models import pg_enum


class PaymentStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class PayoutStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"


class RefundStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Payout(Base):
    """Выплата хозяину / владельцу машины по оплаченным платежам брони (R-35)."""

    __tablename__ = "payouts"
    __table_args__ = (
        CheckConstraint("amount > 0"),
        Index("payouts_booking_idx", "booking_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    booking_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("bookings.id"))
    recipient_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(CHAR(3), server_default="KGS")
    status: Mapped[PayoutStatus] = mapped_column(
        pg_enum(PayoutStatus, "payout_status"), server_default=PayoutStatus.PENDING.value
    )
    provider_reference: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Payment(Base):
    """Платёж по брони (R-30…R-33). idempotency_key — повтор не создаёт второй платёж."""

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0"),
        CheckConstraint("commission_amount >= 0"),
        CheckConstraint("commission_amount < amount", name="payments_commission_chk"),
        Index("payments_booking_idx", "booking_id"),
        Index(
            "payments_unpaid_out_idx",
            "booking_id",
            postgresql_where=text("status = 'succeeded' AND payout_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    booking_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("bookings.id"))
    payer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    provider: Mapped[str] = mapped_column(Text, server_default="mock")
    provider_transaction_id: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    commission_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(CHAR(3), server_default="KGS")
    status: Mapped[PaymentStatus] = mapped_column(
        pg_enum(PaymentStatus, "payment_status"), server_default=PaymentStatus.PENDING.value
    )
    payout_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("payouts.id"))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BookingActivity(Base):
    """Купленное занятие: снимок названия и цены (R-10); куплено, если платёж succeeded."""

    __tablename__ = "booking_activities"
    __table_args__ = (
        CheckConstraint("persons > 0"),
        CheckConstraint("price_per_person > 0"),
        CheckConstraint(
            "amount = price_per_person * persons", name="booking_activities_amount_chk"
        ),
        Index("booking_activities_booking_idx", "booking_id"),
        Index("booking_activities_payment_idx", "payment_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    booking_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("bookings.id"))
    activity_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("activities.id"))
    activity_name: Mapped[str] = mapped_column(Text)
    persons: Mapped[int] = mapped_column(SmallInteger)
    price_per_person: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    payment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("payments.id", name="booking_activities_payment_fk")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Refund(Base):
    """Возврат платежа при отмене брони (R-34): в MVP — полная сумма платежа."""

    __tablename__ = "refunds"
    __table_args__ = (CheckConstraint("amount > 0"), Index("refunds_payment_idx", "payment_id"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    payment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("payments.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[RefundStatus] = mapped_column(
        pg_enum(RefundStatus, "refund_status"), server_default=RefundStatus.PENDING.value
    )
    initiated_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
