from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.users.models import pg_enum


class ReviewStatus(StrEnum):
    PUBLISHED = "published"
    HIDDEN = "hidden"


class Review(Base):
    """Отзыв гостя о хозяине или владельце машины по завершённой брони (R-26)."""

    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5"),
        UniqueConstraint("booking_id", "author_id", name="reviews_booking_author_uq"),
        Index(
            "reviews_target_idx",
            "target_user_id",
            postgresql_where=text("status = 'published'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    booking_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("bookings.id"))
    author_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    target_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    rating: Mapped[int] = mapped_column(SmallInteger)
    text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ReviewStatus] = mapped_column(
        pg_enum(ReviewStatus, "review_status"), server_default=ReviewStatus.PUBLISHED.value
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
