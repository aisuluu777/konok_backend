import hmac
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import ColumnElement, Row, func, or_, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.clock import today_bishkek
from app.core.errors import ConflictError, DomainError, NotFoundError, PermissionDeniedError
from app.modules.admin.service import get_int_setting, get_setting
from app.modules.bookings.models import Booking, BookingStatus, BookingType
from app.modules.bookings.schemas import StayBookingCreate, VehicleBookingCreate
from app.modules.hosts.models import HostProfile
from app.modules.hosts.service import get_public_profile, visible_host
from app.modules.notifications.service import create_notification
from app.modules.payments.provider import PaymentProvider
from app.modules.payments.service import pay_out, provider_id, refund_payments
from app.modules.users.models import KycStatus, User
from app.modules.vehicles.models import Vehicle
from app.modules.vehicles.service import get_public_vehicle

# Статусы, при которых гость видит точный адрес хозяина (R-06).
_ADDRESS_STATUSES = (BookingStatus.CONFIRMED, BookingStatus.IN_PROGRESS, BookingStatus.COMPLETED)

# Верхняя граница NUMERIC(12,2) — больше в base_amount не поместится.
_MAX_AMOUNT = Decimal("1e10")

# Свободные места по ночам [start, end) — запрос из docs/schema.sql (R-12, R-13).
# Просроченный hold мест не держит, даже если его ещё не перевели в expired (R-20).
_NIGHTS_SQL = text(
    """
    SELECT n::date                                        AS night,
           hp.capacity - COALESCE(SUM(b.guests_count), 0) AS free_seats,
           n::date = ANY (hp.blocked_dates)               AS is_blocked
    FROM host_profiles hp
    CROSS JOIN generate_series(CAST(:start AS date), CAST(:end AS date) - 1, interval '1 day') AS n
    LEFT JOIN bookings b
           ON b.type = 'stay'
          AND b.host_id = hp.user_id
          AND b.status IN ('hold', 'confirmed', 'in_progress')
          AND (b.status <> 'hold' OR b.hold_expires_at > now())
          AND b.start_date <= n::date AND n::date < b.end_date
    WHERE hp.user_id = :host
    GROUP BY n, hp.user_id
    ORDER BY n
    """
)


async def _nights(
    session: AsyncSession, host_id: int, start: date, end: date
) -> Sequence[Row[tuple[date, int, bool]]]:
    result = await session.execute(_NIGHTS_SQL, {"host": host_id, "start": start, "end": end})
    return result.all()


async def get_availability(
    session: AsyncSession, host_id: int, start: date, end: date
) -> Sequence[Row[tuple[date, int, bool]]]:
    """Свободные места по ночам у видимого хозяина (R-03, R-12, R-13)."""
    await get_public_profile(session, host_id)
    return await _nights(session, host_id, start, end)


async def _expire_holds(session: AsyncSession, scope: ColumnElement[bool]) -> None:
    """R-20: просроченные hold → expired (иначе мешают EXCLUDE R-18/R-19).

    Строки блокируем по порядку id: параллельные sweep не уходят в дедлок.
    """
    expired_ids = (
        select(Booking.id)
        .where(
            Booking.status == BookingStatus.HOLD,
            Booking.hold_expires_at <= func.now(),
            scope,
        )
        .order_by(Booking.id)
        .with_for_update()
    )
    await session.execute(
        update(Booking).where(Booking.id.in_(expired_ids)).values(status=BookingStatus.EXPIRED)
    )


async def _check_guest_kyc(session: AsyncSession, guest_id: int) -> None:
    """R-01: бронировать может только пользователь с одобренным KYC."""
    kyc = await session.scalar(select(User.kyc_status).where(User.id == guest_id))
    if kyc != KycStatus.APPROVED:
        raise PermissionDeniedError("KYC approval required to book")


async def _commit_hold(session: AsyncSession, booking: Booking, overlap_message: str) -> Booking:
    session.add(booking)
    try:
        await session.commit()
    except DBAPIError as exc:
        await session.rollback()
        # 23P01 — exclusion_violation; при одновременной вставке EXCLUDE может дать и 40P01.
        if getattr(exc.orig, "sqlstate", None) in ("23P01", "40P01"):
            raise ConflictError(overlap_message) from exc
        raise
    await session.refresh(booking)
    return booking


async def create_stay(session: AsyncSession, guest_id: int, data: StayBookingCreate) -> Booking:
    """hold на проживание. Вся проверка мест — под FOR UPDATE строки хозяина (R-16)."""
    if data.host_id == guest_id:
        raise DomainError("Cannot book a stay with yourself")  # R-05
    if data.start_date < today_bishkek():
        raise DomainError("start_date is in the past")
    if data.arrival_time < time.fromisoformat(await get_setting(session, "checkin_time")):
        raise DomainError("arrival_time is earlier than check-in time")  # R-21
    await _check_guest_kyc(session, guest_id)

    host = await session.scalar(
        select(HostProfile)
        .join(User, User.id == HostProfile.user_id)
        .where(HostProfile.user_id == data.host_id, visible_host())  # R-03
        .with_for_update(of=HostProfile)
    )
    if host is None:
        raise NotFoundError("Host not found")

    # Просроченные hold этого хозяина (места) и этого гостя (EXCLUDE R-18).
    await _expire_holds(session, or_(Booking.host_id == host.user_id, Booking.guest_id == guest_id))

    nights = await _nights(session, host.user_id, data.start_date, data.end_date)
    if any(night.is_blocked for night in nights):
        raise ConflictError("Some nights are blocked by the host")  # R-13
    if any(night.free_seats < data.guests_count for night in nights):
        raise ConflictError("Not enough free seats")  # R-12

    base_amount = host.price_per_person_night * data.guests_count * len(nights)
    if base_amount >= _MAX_AMOUNT:
        raise DomainError("Booking amount is too large")
    hold_minutes = await get_int_setting(session, "hold_minutes")
    booking = Booking(
        type=BookingType.STAY,
        guest_id=guest_id,
        host_id=host.user_id,
        start_date=data.start_date,
        end_date=data.end_date,
        # R-17: снимок цены; R-33: снимок комиссии.
        unit_price=host.price_per_person_night,
        base_amount=base_amount,
        commission_rate=Decimal(await get_setting(session, "commission_rate")),
        hold_expires_at=func.now() + timedelta(minutes=hold_minutes),
        guests_count=data.guests_count,
        arrival_time=data.arrival_time,
    )
    return await _commit_hold(session, booking, "You already have a stay on these dates")  # R-18


async def create_vehicle_booking(
    session: AsyncSession, guest_id: int, data: VehicleBookingCreate
) -> Booking:
    """hold на аренду машины. Пересечения дат одной машины запрещает EXCLUDE (R-19)."""
    if data.start_date < today_bishkek():
        raise DomainError("start_date is in the past")
    await _check_guest_kyc(session, guest_id)
    vehicle = await get_public_vehicle(session, data.vehicle_id)  # R-03
    if vehicle.owner_id == guest_id:
        raise DomainError("Cannot rent your own vehicle")  # R-05

    await _expire_holds(session, Booking.vehicle_id == vehicle.id)

    base_amount = vehicle.price_per_day * (data.end_date - data.start_date).days
    if base_amount >= _MAX_AMOUNT:
        raise DomainError("Booking amount is too large")
    hold_minutes = await get_int_setting(session, "hold_minutes")
    booking = Booking(
        type=BookingType.VEHICLE,
        guest_id=guest_id,
        vehicle_id=vehicle.id,
        start_date=data.start_date,
        end_date=data.end_date,
        # R-17: снимок цены за день; R-33: снимок комиссии.
        unit_price=vehicle.price_per_day,
        base_amount=base_amount,
        commission_rate=Decimal(await get_setting(session, "commission_rate")),
        hold_expires_at=func.now() + timedelta(minutes=hold_minutes),
        pickup_location=data.pickup_location,
        return_location=data.return_location,
    )
    return await _commit_hold(session, booking, "Vehicle is already booked on these dates")


async def get_booking(
    session: AsyncSession, user_id: int, booking_id: int
) -> tuple[Booking, str | None]:
    """Бронь видят гость и хозяин / владелец машины; адрес — только гостю после оплаты (R-06)."""
    booking = await session.get(Booking, booking_id)
    if booking is None:
        raise NotFoundError("Booking not found")
    provider_id = booking.host_id
    if booking.vehicle_id is not None:
        provider_id = await session.scalar(
            select(Vehicle.owner_id).where(Vehicle.id == booking.vehicle_id)
        )
    if user_id not in (booking.guest_id, provider_id):
        raise NotFoundError("Booking not found")
    address = None
    if (
        booking.host_id is not None
        and user_id == booking.guest_id
        and booking.status in _ADDRESS_STATUSES
    ):
        address = await session.scalar(
            select(HostProfile.address).where(HostProfile.user_id == booking.host_id)
        )
    return booking, address


async def check_in(session: AsyncSession, user_id: int, booking_id: int, pin: str) -> Booking:
    """Заселение / передача машины (R-23): confirmed → in_progress по PIN гостя.

    PIN принимается с дня заезда до дня выезда (start_date <= сегодня < end_date).
    Верный PIN у stay запускает выплату хозяину (R-35); у аренды машины выплата — при
    completed. Неверный PIN только увеличивает счётчик, без блокировки.
    """
    booking = await session.scalar(
        select(Booking).where(Booking.id == booking_id).with_for_update()
    )
    if booking is None:
        raise NotFoundError("Booking not found")
    if user_id != await provider_id(session, booking):
        raise NotFoundError("Booking not found")
    if booking.status != BookingStatus.CONFIRMED:
        raise ConflictError("Booking is not awaiting check-in")
    if not booking.start_date <= today_bishkek() < booking.end_date:
        raise ConflictError("Check-in is possible only from start date until end date")

    guest_pin = await session.scalar(select(User.checkin_pin).where(User.id == booking.guest_id))
    if guest_pin is None or not hmac.compare_digest(guest_pin, pin):
        booking.checkin_pin_attempts += 1
        await session.commit()  # счётчик сохраняем, ошибку отдаём после коммита
        raise DomainError("Invalid PIN")

    now: datetime = await session.scalar(select(func.now()))  # type: ignore[assignment]
    booking.status = BookingStatus.IN_PROGRESS
    booking.checked_in_at = now
    if booking.type == BookingType.STAY:
        await pay_out(session, booking, now)
    create_notification(
        session, user_id=booking.guest_id, type="checked_in", payload={"booking_id": booking.id}
    )
    await session.commit()
    await session.refresh(booking)
    return booking


async def cancel_booking(
    session: AsyncSession,
    provider: PaymentProvider,
    user_id: int,
    booking_id: int,
    reason: str | None,
) -> Booking:
    """Отмена гостем (R-22, R-34): hold или confirmed → cancelled_by_guest, полный возврат.

    После заселения (in_progress) выплата хозяину уже проведена — отмена не поддерживается.
    С дня выезда (end_date) отмены нет: бронь закрывается как completed / no_show (R-24).
    Хозяин / владелец машины отменить не может — 403 (R-22).
    """
    booking = await session.scalar(
        select(Booking).where(Booking.id == booking_id).with_for_update()
    )
    if booking is None:
        raise NotFoundError("Booking not found")
    if user_id != booking.guest_id:
        if user_id == await provider_id(session, booking):
            raise PermissionDeniedError("Only the guest can cancel a booking")  # R-22
        raise NotFoundError("Booking not found")
    if booking.status not in (BookingStatus.HOLD, BookingStatus.CONFIRMED):
        raise ConflictError("Booking can no longer be cancelled")
    if today_bishkek() >= booking.end_date:
        raise ConflictError("Booking period has ended")

    now: datetime = await session.scalar(select(func.now()))  # type: ignore[assignment]
    previous = booking.status
    booking.status = BookingStatus.CANCELLED_BY_GUEST
    booking.cancelled_at = now
    booking.cancelled_by = user_id
    booking.cancel_reason = reason
    refunds = await refund_payments(
        session, provider, booking, initiated_by=user_id, reason=reason, now=now
    )
    write_audit(  # R-28
        session,
        actor_id=user_id,
        action="booking_cancelled",
        entity_type="booking",
        entity_id=booking.id,
        details={
            "previous_status": previous.value,
            "reason": reason,
            "refunded": str(sum((r.amount for r in refunds), Decimal(0))),
        },
    )
    await session.commit()
    await session.refresh(booking)
    return booking


async def close_booking(session: AsyncSession, booking_id: int, outcome: BookingStatus) -> Booking:
    """Закрытие модератором / админом: completed или no_show, не раньше end_date (R-24, R-35).

    stay: in_progress → completed (выплата уже была); confirmed → no_show + выплата хозяину.
    vehicle: confirmed | in_progress → completed + выплата владельцу; no_show только у stay.
    """
    booking = await session.scalar(
        select(Booking).where(Booking.id == booking_id).with_for_update()
    )
    if booking is None:
        raise NotFoundError("Booking not found")
    if outcome == BookingStatus.NO_SHOW:
        if booking.type != BookingType.STAY:
            raise DomainError("Only a stay can be marked as no-show")
        allowed: tuple[BookingStatus, ...] = (BookingStatus.CONFIRMED,)
    elif booking.type == BookingType.STAY:
        allowed = (BookingStatus.IN_PROGRESS,)
    else:
        allowed = (BookingStatus.CONFIRMED, BookingStatus.IN_PROGRESS)
    if booking.status not in allowed:
        raise ConflictError("Booking status does not allow this transition")
    if today_bishkek() < booking.end_date:
        raise ConflictError("Booking period has not ended yet")

    now: datetime = await session.scalar(select(func.now()))  # type: ignore[assignment]
    booking.status = outcome
    if outcome == BookingStatus.COMPLETED:
        booking.completed_at = now
    # Выплата по платежам без payout_id: у stay-completed таких нет, остальным — первая выплата.
    await pay_out(session, booking, now)
    if outcome == BookingStatus.NO_SHOW:
        payload = {"booking_id": booking.id}
        for user_id in (booking.guest_id, await provider_id(session, booking)):
            create_notification(session, user_id=user_id, type="no_show", payload=payload)
    await session.commit()
    await session.refresh(booking)
    return booking
