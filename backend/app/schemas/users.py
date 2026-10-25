from datetime import datetime

from pydantic import BaseModel, Field

from app.models import UserRole
from app.schemas.common import Schema


class UserMe(Schema):
    id: int
    email: str
    full_name: str
    role: UserRole
    is_verified: bool = Field(description="Документ (ID) одобрен admin — можно бронировать")
    verified_at: datetime | None
    is_driver_license_verified: bool = Field(description="Права одобрены — можно арендовать авто")
    has_id_document: bool
    has_driver_license: bool
    checkin_pin: str = Field(description="PIN заселения: гость называет его хозяину при заезде")
    created_at: datetime


class UserUpdate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)


class UserShort(Schema):
    id: int
    full_name: str
