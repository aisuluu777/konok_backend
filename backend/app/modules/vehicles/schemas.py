from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.db import SmallId
from app.core.storage import IMAGE_URL_PATTERN

Model = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
ImageUrl = Annotated[str, StringConstraints(pattern=IMAGE_URL_PATTERN)]
Photos = Annotated[list[ImageUrl], Field(max_length=20)]
Year = Annotated[int, Field(ge=1900, le=2100)]
Seats = Annotated[int, Field(ge=1, le=100)]
# Пишет любой user, уходит в публичный каталог — ограничиваем размер.
SpecText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Specs = Annotated[dict[SpecText, SpecText], Field(max_length=30)]
Included = Annotated[list[SpecText], Field(max_length=30)]
# Деньги — Decimal, как NUMERIC(12,2) в БД; цена за день (R-17), сом (R-36).
Price = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]


class VehicleCreate(BaseModel):
    region_id: SmallId
    model: Model
    price_per_day: Price
    year: Year | None = None
    seats: Seats | None = None
    transmission: ShortText | None = None
    fuel: ShortText | None = None
    specs: Specs = {}
    included: Included = []
    photos: Photos = []
    with_driver: bool = False
    is_published: bool = True  # R-40


class VehicleUpdate(BaseModel):
    """Частичное изменение: null в year/seats/transmission/fuel очищает поле, иначе — не менять."""

    region_id: SmallId | None = None
    model: Model | None = None
    price_per_day: Price | None = None
    year: Year | None = None
    seats: Seats | None = None
    transmission: ShortText | None = None
    fuel: ShortText | None = None
    specs: Specs | None = None
    included: Included | None = None
    photos: Photos | None = None
    with_driver: bool | None = None
    is_published: bool | None = None


class VehiclePublicOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    region_id: int
    model: str
    year: int | None
    seats: int | None
    transmission: str | None
    fuel: str | None
    specs: dict[str, Any]
    included: list[Any]
    photos: list[str]
    price_per_day: Decimal
    with_driver: bool
    rating_avg: Decimal
    reviews_count: int


class VehicleOwnerOut(VehiclePublicOut):
    is_published: bool
    created_at: datetime
    updated_at: datetime
