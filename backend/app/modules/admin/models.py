from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# TODO: Зачем это таблица?
class Setting(Base):
    """Настройки платформы: ключ — значение строкой, тип знает читающий код."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", name="settings_updated_by_fk")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Region(Base):
    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)

# TODO: таблица тегов была в activities/models.py, но она  относится к админке. Почему в activities/models тоже добавил?
class Tag(Base):
    """Теги занятий: список ведёт администратор, хозяин выбирает (R-09)."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)

# TODO: где поле фото?
class Place(Base):
    """Справочник мест: управляет admin, читают все (R-37)."""

    __tablename__ = "places"
    __table_args__ = (Index("places_region_idx", "region_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    region_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("regions.id"))
    short_description: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(Text)
    stops: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    travel_options: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
