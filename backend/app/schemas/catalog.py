"""Справочники: регионы, локации, теги, места."""

from datetime import datetime
from decimal import Decimal

from app.schemas.common import Photo, Schema


class RegionOut(Schema):
    id: int
    name_ky: str
    name_ru: str
    name_en: str
    slug: str


class LocalityOut(Schema):
    id: int
    region_id: int
    name_ky: str
    name_ru: str
    name_en: str
    slug: str
    lat: Decimal | None
    lng: Decimal | None


class TagOut(Schema):
    id: int
    name_ky: str
    name_ru: str
    name_en: str


class PlaceOut(Schema):
    id: int
    locality_id: int
    name_ky: str
    name_ru: str
    name_en: str
    description_ky: str | None
    description_ru: str | None
    description_en: str | None
    photos: list[Photo]
    created_at: datetime
