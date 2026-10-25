from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Vehicle(Base):
    __tablename__ = "vehicles"
    __table_args__ = (CheckConstraint("price_per_day > 0", name="price_positive"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    locality_id: Mapped[int] = mapped_column(ForeignKey("localities.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))  # марка и модель, например «Toyota RAV4»
    description_ky: Mapped[str | None] = mapped_column(Text)
    description_ru: Mapped[str | None] = mapped_column(Text)
    description_en: Mapped[str | None] = mapped_column(Text)
    price_per_day: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    is_published: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    photos: Mapped[list["VehiclePhoto"]] = relationship(
        cascade="all, delete-orphan", order_by="VehiclePhoto.id", lazy="selectin"
    )

    def __str__(self) -> str:
        return self.title


class VehiclePhoto(Base):
    __tablename__ = "vehicle_photos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VehicleBlockedDate(Base):
    """День, закрытый для аренды авто (admin при отмене по неуважительной причине)."""

    __tablename__ = "vehicle_blocked_dates"

    vehicle_id: Mapped[int] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[date] = mapped_column(Date, primary_key=True)
