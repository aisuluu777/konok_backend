from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class HostProfile(Base):
    """Профиль хозяина: один на пользователя, id профиля = id пользователя."""

    __tablename__ = "host_profiles"
    __table_args__ = (
        CheckConstraint("capacity > 0", name="capacity_positive"),
        CheckConstraint("price_per_person > 0", name="price_positive"),
    )

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    locality_id: Mapped[int] = mapped_column(ForeignKey("localities.id"), index=True)
    address: Mapped[str] = mapped_column(Text)
    description_ky: Mapped[str | None] = mapped_column(Text)
    description_ru: Mapped[str | None] = mapped_column(Text)
    description_en: Mapped[str | None] = mapped_column(Text)
    capacity: Mapped[int]
    price_per_person: Mapped[Decimal] = mapped_column(Numeric(12, 2))  # за ночь
    is_published: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    photos: Mapped[list["HostPhoto"]] = relationship(
        cascade="all, delete-orphan", order_by="HostPhoto.id", lazy="selectin"
    )

    def __str__(self) -> str:
        return f"Хозяин #{self.user_id}"


class HostPhoto(Base):
    __tablename__ = "host_photos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    host_id: Mapped[int] = mapped_column(
        ForeignKey("host_profiles.user_id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HostBlockedDate(Base):
    """День, закрытый хозяином (или admin при отмене по неуважительной причине)."""

    __tablename__ = "host_blocked_dates"

    host_id: Mapped[int] = mapped_column(
        ForeignKey("host_profiles.user_id", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[date] = mapped_column(Date, primary_key=True)
