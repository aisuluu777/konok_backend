import uuid
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, DomainError, NotFoundError
from app.modules.activities.models import Activity
from app.modules.bookings.models import Booking, BookingStatus, BookingType
from app.modules.notifications.service import create_notification
from app.modules.payments.models import (
    BookingActivity,
    Payment,
    PaymentStatus,
    Payout,
    PayoutStatus,
    Refund,
    RefundStatus,
)
from app.modules.payments.provider import PaymentProvider
from app.modules.payments.schemas import ActivityChoice, AddActivitiesIn, PayIn
from app.modules.vehicles.models import Vehicle

# Верхняя граница NUMERIC(12,2).
_MAX_AMOUNT = Decimal("1e10")


def commission(amount: Decimal, rate: Decimal) -> Decimal:
    """R-33: round(amount × commission_rate, 2), половина — от нуля (как round в PostgreSQL)."""
    return (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def _chosen_activities(
    session: AsyncSession, booking: Booking, choices: list[ActivityChoice]
) -> list[BookingActivity]:
    """Занятия — только у брони stay, активные занятия её хозяина, людей ≤ гостей (R-11)."""
    if not choices:
        return []
    if booking.type != BookingType.STAY:
        raise DomainError("Activities can be bought only with a stay")
    rows: list[BookingActivity] = []
    for choice in choices:
        activity = await session.scalar(
            select(Activity).where(
                Activity.id == choice.activity_id,
                Activity.host_id == booking.host_id,
                Activity.is_active.is_(True),
            )
        )
        if activity is None:
            raise NotFoundError("Activity not found")
        if choice.persons > (booking.guests_count or 0):
            raise DomainError("Activity persons exceed booking guests")  # R-11
        rows.append(
            BookingActivity(
                booking_id=booking.id,
                activity_id=activity.id,
                # R-10: снимок названия и цены.
                activity_name=activity.name,
                persons=choice.persons,
                price_per_person=activity.price_per_person,
                amount=activity.price_per_person * choice.persons,
            )
        )
    return rows


async def provider_id(session: AsyncSession, booking: Booking) -> int:
    """Кому «новая бронь»: хозяин или владелец машины."""
    if booking.host_id is not None:
        return booking.host_id
    owner_id = await session.scalar(
        select(Vehicle.owner_id).where(Vehicle.id == booking.vehicle_id)
    )
    assert owner_id is not None  # FK
    return owner_id


async def pay_out(session: AsyncSession, booking: Booking, now: datetime) -> Payout | None:
    """R-35: одна выплата по всем succeeded-платежам брони без payout_id (сумма минус комиссия).

    Mock-провайдер сразу помечает её paid. Вызывающий держит FOR UPDATE брони и коммитит.
    """
    payments = (
        await session.scalars(
            select(Payment).where(
                Payment.booking_id == booking.id,
                Payment.status == PaymentStatus.SUCCEEDED,
                Payment.payout_id.is_(None),
            )
        )
    ).all()
    if not payments:
        return None
    recipient_id = await provider_id(session, booking)
    payout = Payout(
        booking_id=booking.id,
        recipient_id=recipient_id,
        amount=sum((p.amount - p.commission_amount for p in payments), Decimal(0)),
        currency=booking.currency,
        status=PayoutStatus.PAID,
        provider_reference=f"mock-{uuid.uuid4().hex}",
        paid_at=now,
    )
    session.add(payout)
    await session.flush()
    for payment in payments:
        payment.payout_id = payout.id
    create_notification(
        session, user_id=recipient_id, type="payout_paid", payload={"booking_id": booking.id}
    )
    return payout


async def refund_payments(
    session: AsyncSession,
    provider: PaymentProvider,
    booking: Booking,
    *,
    initiated_by: int,
    reason: str | None,
    now: datetime,
) -> list[Refund]:
    """R-34: полный возврат каждого succeeded-платежа брони; платёж → refunded.

    Вызывающий держит FOR UPDATE брони и коммитит. Гостю — одно уведомление, если было что вернуть.
    """
    payments = (
        await session.scalars(
            select(Payment).where(
                Payment.booking_id == booking.id, Payment.status == PaymentStatus.SUCCEEDED
            )
        )
    ).all()
    refunds: list[Refund] = []
    for payment in payments:
        ok = await provider.refund(
            transaction_id=payment.provider_transaction_id, amount=payment.amount
        )
        refund = Refund(
            payment_id=payment.id,
            amount=payment.amount,
            reason=reason,
            status=RefundStatus.SUCCEEDED if ok else RefundStatus.FAILED,
            initiated_by=initiated_by,
            processed_at=now,
        )
        if ok:
            payment.status = PaymentStatus.REFUNDED
        refunds.append(refund)
    session.add_all(refunds)
    if refunds:
        create_notification(
            session,
            user_id=booking.guest_id,
            type="refund",
            payload={
                "booking_id": booking.id,
                "amount": str(sum((r.amount for r in refunds), Decimal(0))),
            },
        )
    return refunds


async def _lock_own_booking(session: AsyncSession, payer_id: int, booking_id: int) -> Booking:
    """Блокирует строку брони: параллельные оплаты одной брони выстраиваются в очередь."""
    booking = await session.scalar(
        select(Booking).where(Booking.id == booking_id).with_for_update()
    )
    if booking is None or booking.guest_id != payer_id:
        raise NotFoundError("Booking not found")
    return booking


async def _existing_payment(
    session: AsyncSession, booking: Booking, idempotency_key: str
) -> Payment | None:
    """R-32: повтор с тем же ключом возвращает прежний платёж; ключ чужой брони — 409."""
    existing = await session.scalar(
        select(Payment).where(Payment.idempotency_key == idempotency_key)
    )
    if existing is not None and existing.booking_id != booking.id:
        raise ConflictError("Idempotency key is already used")
    return existing


async def _charge(
    session: AsyncSession,
    provider: PaymentProvider,
    booking: Booking,
    *,
    payer_id: int,
    idempotency_key: str,
    base_amount: Decimal,
    activities: list[BookingActivity],
    now: datetime,
) -> Payment:
    """Создаёт платёж на base_amount + занятия и вызывает провайдера. Статус брони не меняет."""
    amount = base_amount + sum((a.amount for a in activities), Decimal(0))
    if amount >= _MAX_AMOUNT:
        raise DomainError("Payment amount is too large")

    commission_amount = commission(amount, booking.commission_rate)  # R-33
    if commission_amount >= amount:  # payments_commission_chk: при крайней ставке и малой сумме
        raise DomainError("Payment amount is too small for the commission rate")
    payment = Payment(
        booking_id=booking.id,
        payer_id=payer_id,
        provider=provider.name,
        idempotency_key=idempotency_key,
        amount=amount,
        commission_amount=commission_amount,
        currency=booking.currency,
        status=PaymentStatus.PENDING,
    )
    session.add(payment)
    try:
        await session.flush()  # UNIQUE idempotency_key проверяется здесь
    except IntegrityError as exc:
        await session.rollback()
        if getattr(exc.orig, "sqlstate", None) == "23505":  # тот же ключ у другой брони параллельно
            raise ConflictError("Idempotency key is already used") from exc
        raise
    for activity in activities:
        activity.payment_id = payment.id
    session.add_all(activities)

    result = await provider.charge(
        amount=amount, currency=booking.currency, idempotency_key=idempotency_key
    )
    if result.success:
        payment.status = PaymentStatus.SUCCEEDED
        payment.provider_transaction_id = result.transaction_id
        payment.paid_at = now
    else:
        payment.status = PaymentStatus.FAILED
    return payment


async def pay_booking(
    session: AsyncSession,
    provider: PaymentProvider,
    *,
    payer_id: int,
    booking_id: int,
    data: PayIn,
) -> tuple[Payment, Booking]:
    """Оплата hold (R-31): успех → платёж succeeded, бронь confirmed; отказ → failed, бронь hold.

    Строка брони блокируется: две оплаты одной брони не пройдут обе, а повтор с тем же
    idempotency_key дождётся первой и вернёт её платёж (R-32).
    """
    booking = await _lock_own_booking(session, payer_id, booking_id)
    existing = await _existing_payment(session, booking, data.idempotency_key)
    if existing is not None:
        return existing, booking

    if booking.status != BookingStatus.HOLD:
        raise ConflictError("Booking is not awaiting payment")
    # Время — из БД, как в sweep и подсчёте мест (R-20).
    now: datetime = await session.scalar(select(func.now()))  # type: ignore[assignment]
    if booking.hold_expires_at <= now:
        raise ConflictError("Hold has expired")  # R-20

    activities = await _chosen_activities(session, booking, data.activities)
    payment = await _charge(
        session,
        provider,
        booking,
        payer_id=payer_id,
        idempotency_key=data.idempotency_key,
        base_amount=booking.base_amount,
        activities=activities,
        now=now,
    )
    if payment.status == PaymentStatus.SUCCEEDED:
        booking.status = BookingStatus.CONFIRMED
        booking.confirmed_at = now
        # R-39: гостю — подтверждение, хозяину / владельцу машины — «новая бронь».
        payload = {"booking_id": booking.id}
        create_notification(
            session, user_id=booking.guest_id, type="booking_confirmed", payload=payload
        )
        create_notification(
            session,
            user_id=await provider_id(session, booking),
            type="new_booking",
            payload=payload,
        )
    # Отказ: бронь остаётся hold до истечения (R-31).

    await session.commit()
    await session.refresh(payment)
    await session.refresh(booking)
    return payment, booking


async def buy_activities(
    session: AsyncSession,
    provider: PaymentProvider,
    *,
    payer_id: int,
    booking_id: int,
    data: AddActivitiesIn,
) -> tuple[Payment, Booking]:
    """Докупка занятий (R-11): только к stay в статусе confirmed / in_progress.

    Отдельный платёж на сумму занятий с комиссией брони (R-33); статус брони не меняется.
    При отказе провайдера платёж failed, занятия считаются не купленными.
    """
    booking = await _lock_own_booking(session, payer_id, booking_id)
    existing = await _existing_payment(session, booking, data.idempotency_key)
    if existing is not None:
        return existing, booking

    if booking.status not in (BookingStatus.CONFIRMED, BookingStatus.IN_PROGRESS):
        raise ConflictError("Activities can be added only to a confirmed or in-progress booking")
    now: datetime = await session.scalar(select(func.now()))  # type: ignore[assignment]

    activities = await _chosen_activities(session, booking, data.activities)
    payment = await _charge(
        session,
        provider,
        booking,
        payer_id=payer_id,
        idempotency_key=data.idempotency_key,
        base_amount=Decimal(0),
        activities=activities,
        now=now,
    )
    if payment.status == PaymentStatus.SUCCEEDED:
        # R-39: успешная оплата докупки — гостю и хозяину / владельцу.
        payload = {"booking_id": booking.id, "payment_id": payment.id}
        for user_id in (booking.guest_id, await provider_id(session, booking)):
            create_notification(session, user_id=user_id, type="activities_paid", payload=payload)
        if booking.status == BookingStatus.IN_PROGRESS:
            await pay_out(
                session, booking, now
            )  # R-35: докупка после заселения — отдельная выплата
    await session.commit()
    await session.refresh(payment)
    await session.refresh(booking)
    return payment, booking
