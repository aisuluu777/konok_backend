from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    Table,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.modules.admin.models import Tag

#TODO где поле название? как админ может добавить занятие без названия? Также думаю, что foreing key в этой таблице не нужен, так как занятие может быть удалено, а тег останется. Поэтому foreign key должен быть в таблице Activity на activity_tags.
# Теги занятия: хозяин выбирает из админского списка tags (R-09).
c = Table(
    "activity_tags",
    Base.metadata,
    Column(
        "activity_id",
        BigInteger,
        ForeignKey("activities.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("tag_id", SmallInteger, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    Index("activity_tags_tag_idx", "tag_id"),
)


class Activity(Base):
    """Занятие хозяина (R-08). Не удаляется — только is_active = false (R-10)."""

    __tablename__ = "activities"
    __table_args__ = (
        CheckConstraint("price_per_person > 0"),
        Index("activities_host_idx", "host_id", postgresql_where=text("is_active")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    host_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("host_profiles.user_id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    price_per_person: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # selectin: в async ленивой загрузки нет, теги грузятся сразу вместе с занятием.
    tags: Mapped[list[Tag]] = relationship(
        secondary=activity_tags, lazy="selectin", order_by=Tag.id
    )
