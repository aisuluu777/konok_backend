from fastapi import APIRouter
from sqlalchemy import select

from app import rules
from app.config import get_settings
from app.deps import Session
from app.errors import conflict, not_found
from app.models import Booking, BookingStatus, Payment, PaymentStatus
from app.routers.bookings import booking_out
from app.schemas.bookings import BookingOut
from app.security import CurrentUser

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("/{payment_id}/mock-pay")
async def mock_pay(payment_id: int, me: CurrentUser, session: Session) -> BookingOut:
    """Имитация успешной оплаты (только при PAYMENT_PROVIDER=mock).

    Помечает платёж оплаченным; бронь в статусе pending_payment становится confirmed.
    Повторный вызов возвращает тот же результат.
    """
    if get_settings().payment_provider != "mock":
        raise not_found("Mock-оплата отключена")
    payment = await session.get(Payment, payment_id)
    if payment is None:
        raise not_found("Платёж не найден")
    booking = await session.scalar(
        select(Booking).where(Booking.id == payment.booking_id).with_for_update()
    )
    if booking is None or booking.guest_id != me.id:
        raise not_found("Платёж не найден")
    await session.refresh(payment)  # статус после блокировки брони
    if payment.status == PaymentStatus.PAID:
        return await booking_out(session, booking, me)
    if payment.status != PaymentStatus.PENDING:
        raise conflict("Платёж отменён")
    if booking.status == BookingStatus.PENDING_PAYMENT:
        booking.status = BookingStatus.CONFIRMED
    elif booking.status not in rules.PAID_STATUSES:
        raise conflict("Бронь отменена или завершена — оплата невозможна")

    payment.status = PaymentStatus.PAID
    payment.paid_at = rules.now_bishkek()
    await session.commit()
    await session.refresh(booking, ["payments", "activities"])
    return await booking_out(session, booking, me)
