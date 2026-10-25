from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Region(Base):
    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name_ky: Mapped[str] = mapped_column(String(200))
    name_ru: Mapped[str] = mapped_column(String(200))
    name_en: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    is_active: Mapped[bool] = mapped_column(default=True)

    def __str__(self) -> str:
        return self.name_ru


class Locality(Base):
    __tablename__ = "localities"

    id: Mapped[int] = mapped_column(primary_key=True)
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"), index=True)
    name_ky: Mapped[str] = mapped_column(String(200))
    name_ru: Mapped[str] = mapped_column(String(200))
    name_en: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    is_active: Mapped[bool] = mapped_column(default=True)

    def __str__(self) -> str:
        return self.name_ru
