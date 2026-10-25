from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from app.models import BookingStatus, BookingType, PaymentStatus
from app.schemas.common import Language, Schema


class ActivityChoice(BaseModel):
    activity_id: int
    persons: int = Field(gt=0, description="Сколько человек; не больше гостей в брони")


class _Dates(BaseModel):
    start_date: date = Field(description="Дата заезда / получения авто")
    end_date: date = Field(description="Дата выезда / возврата авто (не входит в оплату)")

    @model_validator(mode="after")
    def dates_order(self) -> "_Dates":
        if self.end_date <= self.start_date:
            raise ValueError("end_date должна быть позже start_date")
        return self


class StayBookingCreate(_Dates):
    host_id: int
    guests_count: int = Field(gt=0)
    activities: list[ActivityChoice] = Field(default=[], description="Оплатить вместе с бронью")


class VehicleBookingCreate(_Dates):
    vehicle_id: int


class AddActivitiesIn(BaseModel):
    activities: list[ActivityChoice] = Field(min_length=1)


class CancelIn(BaseModel):
    reason: str | None = None


class CheckInIn(BaseModel):
    pin: str = Field(pattern=r"^\d{6}$", description="PIN гостя из его личного кабинета")


class ReviewCreate(BaseModel):
    """Отзыв пишется на одном языке; переводы добавляет admin."""

    rating: int = Field(ge=1, le=5)
    text: str | None = None
    language: Language = Field(description="Язык, на котором написан текст")


class PaymentOut(Schema):
    id: int
    pg_order_id: str
    amount: Decimal
    refunded_amount: Decimal
    status: PaymentStatus
    paid_at: datetime | None
    created_at: datetime


class BookingActivityOut(Schema):
    id: int
    activity_id: int
    payment_id: int
    persons: int
    price_per_person: Decimal
    amount: Decimal


class BookingOut(Schema):
    id: int
    type: BookingType
    status: BookingStatus = Field(
        description="pending_payment — идёт оплата (места уже заняты); confirmed — оплачена; "
        "in_progress — заселение по PIN; completed — завершена (12:00 дня выезда); "
        "неоплаченная бронь удаляется через 30 минут; "
        "cancelled_by_guest / cancelled_by_admin — отменена; "
        "failed — к 12:00 дня выезда заселения по PIN не было"
    )
    guest_id: int
    host_id: int | None
    vehicle_id: int | None
    start_date: date
    end_date: date
    guests_count: int | None
    unit_price: Decimal = Field(description="Цена за человека-ночь (жильё) или за день (авто)")
    amount: Decimal = Field(description="Стоимость проживания / аренды без активностей")
    host_acknowledged_at: datetime | None = Field(description="Хозяин нажал «Вижу, жду гостя»")
    checked_in_at: datetime | None
    cancelled_at: datetime | None
    refund_amount: Decimal | None
    created_at: datetime
    activities: list[BookingActivityOut]
    payments: list[PaymentOut]
    address: str | None = Field(
        default=None, description="Адрес хозяина — только гостю и только после оплаты"
    )
    host_amount: Decimal | None = Field(
        default=None,
        description="Только хозяину / владельцу авто: оплачено − возвращено − комиссия",
    )


class PaymentStart(BaseModel):
    """Бронь и платёж к оплате. Для mock-провайдера payment_url = null:
    оплатите вызовом POST /api/v1/payments/{payment.id}/mock-pay."""

    booking: BookingOut
    payment: PaymentOut
    payment_url: str | None


class ReviewOut(Schema):
    id: int
    author_id: int
    host_id: int | None
    vehicle_id: int | None
    rating: int
    text_ky: str | None
    text_ru: str | None
    text_en: str | None
    created_at: datetime
