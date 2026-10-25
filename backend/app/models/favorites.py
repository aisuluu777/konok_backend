from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Favorite(Base):
    """Избранное: ровно одна цель — хозяин, место или авто."""

    __tablename__ = "favorites"
    __table_args__ = (
        CheckConstraint("num_nonnulls(host_id, place_id, vehicle_id) = 1", name="one_target"),
        UniqueConstraint(
            "user_id", "host_id", "place_id", "vehicle_id", postgresql_nulls_not_distinct=True
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    host_id: Mapped[int | None] = mapped_column(
        ForeignKey("host_profiles.user_id", ondelete="CASCADE")
    )
    place_id: Mapped[int | None] = mapped_column(ForeignKey("places.id", ondelete="CASCADE"))
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
