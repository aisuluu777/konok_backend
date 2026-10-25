from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, SmallInteger, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Review(Base):
    """Отзыв о хозяине (host_id) или авто (vehicle_id). Оставить может любой, кроме себя самого.

    Автор пишет на одном языке — текст попадает в поле этого языка; остальные заполняет admin.
    """

    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="rating_range"),
        CheckConstraint("num_nonnulls(host_id, vehicle_id) = 1", name="one_target"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    host_id: Mapped[int | None] = mapped_column(ForeignKey("host_profiles.user_id"), index=True)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"), index=True)
    rating: Mapped[int] = mapped_column(SmallInteger)
    text_ky: Mapped[str | None] = mapped_column(Text)
    text_ru: Mapped[str | None] = mapped_column(Text)
    text_en: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
