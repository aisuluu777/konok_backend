from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.core.db import BigId


class ConversationCreate(BaseModel):
    provider_id: BigId  # хозяин или владелец машины; гость — текущий пользователь


class MessageCreate(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    guest_id: int
    provider_id: int
    last_message_at: datetime | None
    created_at: datetime


class ConversationListItem(ConversationOut):
    unread_count: int


class MessageOut(BaseModel):
    id: int
    conversation_id: int
    sender_id: int
    text: str
    read_at: datetime | None
    created_at: datetime
