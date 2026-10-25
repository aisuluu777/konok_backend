from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Tag(Base):
    """Теги активностей создаёт только admin."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name_ky: Mapped[str] = mapped_column(String(100))
    name_ru: Mapped[str] = mapped_column(String(100))
    name_en: Mapped[str] = mapped_column(String(100))

    def __str__(self) -> str:
        return self.name_ru


activity_tags = Table(
    "activity_tags",
    Base.metadata,
    Column("activity_id", ForeignKey("activities.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (CheckConstraint("price_per_person > 0", name="price_positive"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("host_profiles.user_id"), index=True)
    title_ky: Mapped[str | None] = mapped_column(String(200))
    title_ru: Mapped[str | None] = mapped_column(String(200))
    title_en: Mapped[str | None] = mapped_column(String(200))
    description_ky: Mapped[str | None] = mapped_column(Text)
    description_ru: Mapped[str | None] = mapped_column(Text)
    description_en: Mapped[str | None] = mapped_column(Text)
    price_per_person: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tags: Mapped[list[Tag]] = relationship(
        secondary=activity_tags, order_by=Tag.id, lazy="selectin"
    )
    photos: Mapped[list["ActivityPhoto"]] = relationship(
        cascade="all, delete-orphan", order_by="ActivityPhoto.id", lazy="selectin"
    )

    def __str__(self) -> str:
        return self.title_ru or self.title_ky or self.title_en or f"Активность #{self.id}"


class ActivityPhoto(Base):
    __tablename__ = "activity_photos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    activity_id: Mapped[int] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
