from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Vehicle(Base):
    """Машина; владелец — любой user, добавивший её (R-02). Не удаляется (R-40)."""

    __tablename__ = "vehicles"
    __table_args__ = (
        CheckConstraint("price_per_day > 0"),
        Index("vehicles_catalog_idx", "region_id", postgresql_where=text("is_published")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    region_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("regions.id"))
    model: Mapped[str] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(SmallInteger)
    seats: Mapped[int | None] = mapped_column(SmallInteger)
    transmission: Mapped[str | None] = mapped_column(Text)
    fuel: Mapped[str | None] = mapped_column(Text)
    specs: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    included: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    photos: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    price_per_day: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    with_driver: Mapped[bool] = mapped_column(Boolean, server_default="false")
    is_published: Mapped[bool] = mapped_column(Boolean, server_default="false")
    rating_avg: Mapped[Decimal] = mapped_column(Numeric(3, 2), server_default="0")
    reviews_count: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
