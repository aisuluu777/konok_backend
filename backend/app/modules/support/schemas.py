from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.core.db import BigId
from app.modules.support.models import TicketStatus


class TicketCreate(BaseModel):
    # Категория свободная: host_unreachable, emergency, booking_problem, complaint …
    category: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
    ]
    booking_id: BigId | None = None


class StatusIn(BaseModel):
    status: TicketStatus


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int | None
    booking_id: int | None
    category: str
    description: str | None
    status: TicketStatus
    assigned_to: int | None
    created_at: datetime
    updated_at: datetime
