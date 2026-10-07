from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Favorite(Base):
    """Избранное: ровно один объект — хозяин, место или машина (R-38)."""

    __tablename__ = "favorites"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(host_id, place_id, vehicle_id) = 1", name="favorites_one_target_chk"
        ),
        Index(
            "favorites_host_uq",
            "user_id",
            "host_id",
            unique=True,
            postgresql_where=text("host_id IS NOT NULL"),
        ),
        Index(
            "favorites_place_uq",
            "user_id",
            "place_id",
            unique=True,
            postgresql_where=text("place_id IS NOT NULL"),
        ),
        Index(
            "favorites_vehicle_uq",
            "user_id",
            "vehicle_id",
            unique=True,
            postgresql_where=text("vehicle_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    host_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("host_profiles.user_id", ondelete="CASCADE")
    )
    place_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("places.id", ondelete="CASCADE")
    )
    vehicle_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("vehicles.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
