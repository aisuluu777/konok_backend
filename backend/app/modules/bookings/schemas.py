from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.db import BigId
from app.modules.bookings.models import BookingStatus, BookingType

GuestsCount = Annotated[int, Field(ge=1, le=2**15 - 1)]
Location = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class CheckInIn(BaseModel):
    pin: Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")]  # PIN гостя, 6 цифр (R-23)


class CancelIn(BaseModel):
    reason: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
        | None
    ) = None


class StayBookingCreate(BaseModel):
    host_id: BigId
    start_date: date
    end_date: date  # дата выезда, ночь выезда не входит (R-18)
    guests_count: GuestsCount
    arrival_time: time  # не раньше settings.checkin_time (R-21)

    @model_validator(mode="after")
    def dates_order(self) -> Self:
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self


class VehicleBookingCreate(BaseModel):
    vehicle_id: BigId
    start_date: date
    end_date: date  # день возврата не входит в аренду (R-19)
    pickup_location: Location
    return_location: Location

    @model_validator(mode="after")
    def dates_order(self) -> Self:
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self


class BookingOut(BaseModel):
    """Без хеша PIN. host_address — только гостю после оплаты (R-06)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    type: BookingType
    status: BookingStatus
    guest_id: int
    host_id: int | None
    vehicle_id: int | None
    start_date: date
    end_date: date
    unit_price: Decimal
    base_amount: Decimal
    currency: str
    hold_expires_at: datetime
    confirmed_at: datetime | None
    guests_count: int | None
    arrival_time: time | None
    pickup_location: str | None
    return_location: str | None
    created_at: datetime
    host_address: str | None = None


class AvailabilityQuery(BaseModel):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def dates_range(self) -> Self:
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        if (self.end_date - self.start_date).days > 366:
            raise ValueError("period must be at most 366 nights")
        return self


class NightAvailability(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    night: date
    free_seats: int
    is_blocked: bool
