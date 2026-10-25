"""Избранное, чат, жалобы, поддержка."""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models import TicketStatus
from app.schemas.common import Schema
from app.schemas.users import UserShort


class FavoriteCreate(BaseModel):
    """Ровно одно из полей."""

    host_id: int | None = None
    place_id: int | None = None
    vehicle_id: int | None = None

    @model_validator(mode="after")
    def one_target(self) -> "FavoriteCreate":
        if sum(x is not None for x in (self.host_id, self.place_id, self.vehicle_id)) != 1:
            raise ValueError("Укажите ровно одно: host_id, place_id или vehicle_id")
        return self


class FavoriteOut(Schema):
    id: int
    host_id: int | None
    place_id: int | None
    vehicle_id: int | None
    created_at: datetime


class ConversationCreate(BaseModel):
    owner_id: int = Field(description="Хозяин или владелец авто, которому пишет гость")


class ConversationOut(Schema):
    id: int
    guest: UserShort
    owner: UserShort
    created_at: datetime


class MessageCreate(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class MessageOut(Schema):
    id: int
    conversation_id: int
    sender_id: int
    text: str = Field(description="Оригинал")
    text_ky: str | None = Field(description="Перевод; null — показать оригинал")
    text_ru: str | None
    text_en: str | None
    created_at: datetime


class ComplaintCreate(BaseModel):
    user_id: int = Field(description="На кого жалоба")
    message_id: int | None = Field(default=None, description="Сообщение из вашего чата")
    text: str = Field(min_length=1)


class ComplaintOut(Schema):
    id: int
    target_user_id: int
    message_id: int | None
    text: str
    is_resolved: bool
    created_at: datetime


class TicketCreate(BaseModel):
    description: str = Field(min_length=1)
    booking_id: int | None = Field(default=None, description="Связанная бронь")


class SupportMessageCreate(BaseModel):
    text: str = Field(min_length=1)


class SupportMessageOut(Schema):
    id: int
    author_id: int
    text: str
    created_at: datetime


class TicketOut(Schema):
    id: int
    booking_id: int | None
    description: str
    status: TicketStatus
    created_at: datetime


class TicketDetail(TicketOut):
    messages: list[SupportMessageOut]
