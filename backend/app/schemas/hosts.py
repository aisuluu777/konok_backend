from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.common import Photo, Schema


class HostCreate(BaseModel):
    locality_id: int
    address: str = Field(min_length=1, description="Точный адрес; виден гостю после оплаты")
    description_ky: str | None = None
    description_ru: str | None = None
    description_en: str | None = None
    capacity: int = Field(gt=0, description="Сколько гостей можно принять на ночь")
    price_per_person: Decimal = Field(gt=0, max_digits=12, decimal_places=2, description="За ночь")


class HostUpdate(BaseModel):
    locality_id: int | None = None
    address: str | None = Field(default=None, min_length=1)
    description_ky: str | None = None
    description_ru: str | None = None
    description_en: str | None = None
    capacity: int | None = Field(default=None, gt=0)
    price_per_person: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)


class HostMe(Schema):
    user_id: int
    locality_id: int
    address: str
    description_ky: str | None
    description_ru: str | None
    description_en: str | None
    capacity: int
    price_per_person: Decimal
    is_published: bool
    photos: list[Photo]
    created_at: datetime


class HostPublic(BaseModel):
    """Карточка хозяина в каталоге (без точного адреса)."""

    user_id: int
    full_name: str
    locality_id: int
    description_ky: str | None
    description_ru: str | None
    description_en: str | None
    capacity: int
    price_per_person: Decimal
    photos: list[Photo]
    rating: Decimal | None = Field(description="Средняя оценка; null — оценок ещё нет")
    reviews_count: int
    on_platform_since: datetime | None = Field(description="Дата одобрения документов")


class BlockedDatesIn(BaseModel):
    dates: list[date] = Field(min_length=1)


class BlockedDatesOut(BaseModel):
    dates: list[date]


class NightAvailability(BaseModel):
    date: date
    free_seats: int
    is_blocked: bool
