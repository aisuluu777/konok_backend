from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import Schema


class NotificationOut(Schema):
    id: int
    type: str = Field(
        description="host_reminder {booking_id} — хозяин ещё не нажал «Вижу, жду гостя»; "
        "booking_cancelled {booking_id, by: guest|admin}; "
        "new_message {conversation_id, message_id}; support_reply {ticket_id}"
    )
    data: dict[str, Any]
    read_at: datetime | None
    created_at: datetime


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscriptionIn(BaseModel):
    """Результат PushSubscription.toJSON() из браузера."""

    endpoint: str
    keys: PushKeys


class PushUnsubscribeIn(BaseModel):
    endpoint: str


class VapidKeyOut(BaseModel):
    public_key: str = Field(description="applicationServerKey для PushManager.subscribe")
