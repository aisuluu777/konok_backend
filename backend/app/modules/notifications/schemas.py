from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    channel: str
    payload: dict[str, Any]
    read_at: datetime | None
    created_at: datetime


class ReadAllOut(BaseModel):
    updated: int
