from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.db import BigId
from app.modules.bookings.models import BookingStatus
from app.modules.payments.models import PaymentStatus

IdempotencyKey = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class ActivityChoice(BaseModel):
    activity_id: BigId
    persons: Annotated[int, Field(ge=1, le=2**15 - 1)]  # не больше гостей брони — R-11


class PayIn(BaseModel):
    idempotency_key: IdempotencyKey  # R-32
    activities: Annotated[list[ActivityChoice], Field(max_length=50)] = []

    @model_validator(mode="after")
    def unique_activities(self) -> Self:
        ids = [a.activity_id for a in self.activities]
        if len(ids) != len(set(ids)):
            raise ValueError("activities must not repeat")
        return self


class AddActivitiesIn(BaseModel):
    idempotency_key: IdempotencyKey  # R-32
    activities: Annotated[list[ActivityChoice], Field(min_length=1, max_length=50)]

    @model_validator(mode="after")
    def unique_activities(self) -> Self:
        ids = [a.activity_id for a in self.activities]
        if len(ids) != len(set(ids)):
            raise ValueError("activities must not repeat")
        return self


class PaymentOut(BaseModel):
    """Без комиссии и данных провайдера — это внутреннее."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_id: int
    status: PaymentStatus
    amount: Decimal
    currency: str
    idempotency_key: str
    paid_at: datetime | None
    created_at: datetime


class PayOut(BaseModel):
    payment: PaymentOut
    booking_status: BookingStatus
