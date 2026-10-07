from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.core.db import SmallId

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=100)]


class RegionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class TagIn(BaseModel):
    name: NonEmpty


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class PlaceCreate(BaseModel):
    slug: Slug
    name: NonEmpty
    region_id: SmallId
    short_description: str | None = None
    description: str | None = None
    cover_url: str | None = None
    stops: list[Any] = []
    travel_options: list[Any] = []


class PlaceUpdate(BaseModel):
    slug: Slug | None = None
    name: NonEmpty | None = None
    region_id: SmallId | None = None
    short_description: str | None = None
    description: str | None = None
    cover_url: str | None = None
    stops: list[Any] | None = None
    travel_options: list[Any] | None = None


class PlaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    region_id: int
    short_description: str | None
    description: str | None
    cover_url: str | None
    stops: list[Any]
    travel_options: list[Any]
    created_at: datetime


class SettingIn(BaseModel):
    value: NonEmpty


class SettingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    value: str
    updated_by: int | None
    updated_at: datetime
