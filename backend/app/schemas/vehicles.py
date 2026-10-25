from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.common import Photo, Schema


class VehicleCreate(BaseModel):
    locality_id: int
    title: str = Field(min_length=1, max_length=200, description="Марка и модель")
    description_ky: str | None = None
    description_ru: str | None = None
    description_en: str | None = None
    price_per_day: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


class VehicleUpdate(BaseModel):
    locality_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description_ky: str | None = None
    description_ru: str | None = None
    description_en: str | None = None
    price_per_day: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    is_published: bool | None = None


class VehicleMe(Schema):
    id: int
    locality_id: int
    title: str
    description_ky: str | None
    description_ru: str | None
    description_en: str | None
    price_per_day: Decimal
    is_published: bool
    photos: list[Photo]
    created_at: datetime


class VehiclePublic(BaseModel):
    id: int
    owner_id: int
    owner_name: str
    locality_id: int
    title: str
    description_ky: str | None
    description_ru: str | None
    description_en: str | None
    price_per_day: Decimal
    photos: list[Photo]
    rating: Decimal | None
    reviews_count: int


class DayAvailability(BaseModel):
    date: date
    is_free: bool
