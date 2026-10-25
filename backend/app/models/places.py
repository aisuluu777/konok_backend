from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Place(Base):
    """Места добавляет только admin."""

    __tablename__ = "places"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    locality_id: Mapped[int] = mapped_column(ForeignKey("localities.id"), index=True)
    name_ky: Mapped[str] = mapped_column(String(200))
    name_ru: Mapped[str] = mapped_column(String(200))
    name_en: Mapped[str] = mapped_column(String(200))
    description_ky: Mapped[str | None] = mapped_column(Text)
    description_ru: Mapped[str | None] = mapped_column(Text)
    description_en: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    photos: Mapped[list["PlacePhoto"]] = relationship(
        cascade="all, delete-orphan", order_by="PlacePhoto.id", lazy="selectin"
    )

    def __str__(self) -> str:
        return self.name_ru


class PlacePhoto(Base):
    __tablename__ = "place_photos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    place_id: Mapped[int] = mapped_column(ForeignKey("places.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
