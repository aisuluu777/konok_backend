from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class HostProfile(Base):
    """Хозяин = user с этой строкой (R-02). Адрес и координаты — не для публики (R-06)."""

    __tablename__ = "host_profiles"
    __table_args__ = (
        CheckConstraint("video_duration_sec BETWEEN 1 AND 120"),
        CheckConstraint("capacity > 0"),
        CheckConstraint("price_per_person_night > 0"),
        Index("host_profiles_catalog_idx", "region_id", postgresql_where=text("is_published")),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    region_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("regions.id"))
    settlement: Mapped[str] = mapped_column(Text)
    address: Mapped[str] = mapped_column(Text)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    about: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    photos: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    video_url: Mapped[str | None] = mapped_column(Text)
    video_duration_sec: Mapped[int | None] = mapped_column(SmallInteger)
    capacity: Mapped[int] = mapped_column(SmallInteger)
    price_per_person_night: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    blocked_dates: Mapped[list[date]] = mapped_column(ARRAY(Date), server_default="{}")
    is_guide: Mapped[bool] = mapped_column(Boolean, server_default="false")
    is_published: Mapped[bool] = mapped_column(Boolean, server_default="false")
    rating_avg: Mapped[Decimal] = mapped_column(Numeric(3, 2), server_default="0")
    reviews_count: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
