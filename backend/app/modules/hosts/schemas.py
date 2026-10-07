from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.db import SmallId
from app.core.storage import IMAGE_URL_PATTERN, VIDEO_URL_PATTERN

Text200 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Text500 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
ImageUrl = Annotated[str, StringConstraints(pattern=IMAGE_URL_PATTERN)]
VideoUrl = Annotated[str, StringConstraints(pattern=VIDEO_URL_PATTERN)]
# Деньги — Decimal, как NUMERIC(12,2) в БД.
Price = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
Capacity = Annotated[int, Field(ge=1, le=2**15 - 1)]
Latitude = Annotated[Decimal, Field(ge=-90, le=90, max_digits=9, decimal_places=6)]
Longitude = Annotated[Decimal, Field(ge=-180, le=180, max_digits=9, decimal_places=6)]


class HostProfileCreate(BaseModel):
    """Анкета целиком, одним запросом (R-29). Длительность видео сервер берёт из файла (R-07)."""

    region_id: SmallId
    settlement: Text200
    address: Text500
    capacity: Capacity
    price_per_person_night: Price
    about: Annotated[str, StringConstraints(max_length=5000)] | None = None
    avatar_url: ImageUrl | None = None
    photos: Annotated[list[ImageUrl], Field(max_length=20)] = []
    video_url: VideoUrl | None = None
    latitude: Latitude | None = None
    longitude: Longitude | None = None

    @model_validator(mode="after")
    def coordinates_together(self) -> Self:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be set together")
        return self


class HostPublicOut(BaseModel):
    """Публичный вид: без адреса и координат (R-06)."""

    model_config = ConfigDict(from_attributes=True)

    user_id: int
    region_id: int
    settlement: str
    about: str | None
    avatar_url: str | None
    photos: list[str]
    video_url: str | None
    video_duration_sec: int | None
    capacity: int
    price_per_person_night: Decimal
    is_guide: bool
    rating_avg: Decimal
    reviews_count: int


class HostOwnerOut(HostPublicOut):
    address: str
    latitude: Decimal | None
    longitude: Decimal | None
    is_published: bool
    created_at: datetime
    updated_at: datetime


class BlockedDatesIn(BaseModel):
    """Полная замена списка закрытых дней; прошедшие даты отбрасываются (R-14)."""

    dates: Annotated[list[date], Field(max_length=1000)]


class BlockedDatesOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    blocked_dates: list[date]


class CapacityIn(BaseModel):
    capacity: Capacity
