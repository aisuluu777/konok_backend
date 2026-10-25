from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from app.schemas.catalog import TagOut
from app.schemas.common import Photo, Schema


class ActivityCreate(BaseModel):
    """Название — хотя бы на одном языке; остальные переводы допишет admin."""

    title_ky: str | None = None
    title_ru: str | None = None
    title_en: str | None = None
    description_ky: str | None = None
    description_ru: str | None = None
    description_en: str | None = None
    price_per_person: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    tag_ids: list[int] = []

    @model_validator(mode="after")
    def title_required(self) -> "ActivityCreate":
        if not (self.title_ky or self.title_ru or self.title_en):
            raise ValueError("Укажите название хотя бы на одном языке")
        return self


class ActivityUpdate(BaseModel):
    title_ky: str | None = None
    title_ru: str | None = None
    title_en: str | None = None
    description_ky: str | None = None
    description_ru: str | None = None
    description_en: str | None = None
    price_per_person: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    tag_ids: list[int] | None = None
    is_active: bool | None = Field(default=None, description="false — скрыть активность")


class ActivityOut(Schema):
    id: int
    host_id: int
    title_ky: str | None
    title_ru: str | None
    title_en: str | None
    description_ky: str | None
    description_ru: str | None
    description_en: str | None
    price_per_person: Decimal
    is_active: bool
    tags: list[TagOut]
    photos: list[Photo]
    created_at: datetime
