from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.db import SmallId
from app.modules.admin.schemas import TagOut

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Description = Annotated[str, StringConstraints(max_length=5000)]
# Деньги — Decimal, как NUMERIC(12,2) в БД; бесплатных занятий нет (R-08).
Price = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
TagIds = Annotated[list[SmallId], Field(max_length=20)]


class ActivityCreate(BaseModel):
    name: Name
    description: Description | None = None
    price_per_person: Price
    tag_ids: TagIds = []


class ActivityUpdate(BaseModel):
    """Частичное изменение: null в name/price/tag_ids = «не менять», description можно очистить."""

    name: Name | None = None
    description: Description | None = None
    price_per_person: Price | None = None
    tag_ids: TagIds | None = None


class ActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    host_id: int
    name: str
    description: str | None
    price_per_person: Decimal
    tags: list[TagOut]


class ActivityOwnerOut(ActivityOut):
    is_active: bool
    created_at: datetime
    updated_at: datetime
