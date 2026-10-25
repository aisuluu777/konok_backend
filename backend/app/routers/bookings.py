"""Брони проживания и аренды авто.

Бронь создаётся вместе с платежом в статусе pending_payment и сразу занимает места / даты
(проверка — под блокировкой строки хозяина / авто). После оплаты — confirmed.
Если гость отказался от оплаты, бронь удаляется: неоплаченных броней нет.
"""

import uuid
from decimal import Decimal

from fastapi import APIRouter, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import rules
from app.deps import Provider, Session
from app.errors import bad_request, conflict, forbidden, not_found
from app.models import (
    Activity,
    Booking,
    BookingActivity,
    BookingStatus,
    BookingType,
    HostProfile,
    Payment,
    PaymentStatus,
    User,
    Vehicle,
)
from app.notify import notify
from app.pagination import Page, Pagination, paginate
from app.payments import PaymentProvider
from app.schemas.bookings import (
    ActivityChoice,
    AddActivitiesIn,
    BookingOut,
    CancelIn,
    CheckInIn,
    PaymentOut,
    PaymentStart,
    StayBookingCreate,
    VehicleBookingCreate,
)
from app.security import CurrentUser

router = APIRouter(prefix="/bookings", tags=["bookings"])


# --- Общие функции --------------------------------------------------------------


async def provider_id(session: AsyncSession, booking: Booking) -> int:
    """Хозяин или владелец авто."""
    if booking.host_id is not None:
        return booking.host_id
    owner_id = await session.scalar(
        select(Vehicle.owner_id).where(Vehicle.id == booking.vehicle_id)
    )
    assert owner_id is not None
    return owner_id


async def booking_out(session: AsyncSession, booking: Booking, me: User) -> BookingOut:
    out = BookingOut.model_validate(booking)
    if me.id == booking.guest_id:
        if booking.host_id is not None and booking.status in (
            *rules.PAID_STATUSES,
            BookingStatus.COMPLETED,
        ):
            out.address = await session.scalar(
                select(HostProfile.address).where(HostProfile.user_id == booking.host_id)
            )
    else:
        out.host_amount = sum(
            (
                p.amount - p.refunded_amount - p.commission_amount
                for p in booking.payments
                if p.status == PaymentStatus.PAID
            ),
            Decimal(0),
        )
    return out


async def get_booking_for(
    session: AsyncSession, me: User, booking_id: int, *, lock: bool = False
) -> Booking:
    """Бронь видят только гость и хозяин / владелец авто."""
    query = select(Booking).where(Booking.id == booking_id)
    if lock:
        query = query.with_for_update()
    booking = await session.scalar(query)
    if booking is None or me.id not in (booking.guest_id, await provider_id(session, booking)):
        raise not_found("Бронь не найдена")
    return booking


async def _choose_activities(
    session: AsyncSession, booking: Booking, choices: list[ActivityChoice]
) -> list[BookingActivity]:
    """Активности этого хозяина, активные; людей не больше, чем гостей в брони."""
    items = []
    for choice in choices:
        activity = await session.get(Activity, choice.activity_id)
        if activity is None or activity.host_id != booking.host_id or not activity.is_active:
            raise bad_request(f"Активность {choice.activity_id} недоступна")
        if choice.persons > (booking.guests_count or 0):
            raise bad_request("Людей в активности больше, чем гостей в брони")
        items.append(
            BookingActivity(
                activity_id=activity.id,
                persons=choice.persons,
                price_per_person=activity.price_per_person,
                amount=activity.price_per_person * choice.persons,
            )
        )
    return items


async def _start_payment(
    session: AsyncSession,
    provider: PaymentProvider,
    me: User,
    booking: Booking,
    amount: Decimal,
    items: list[BookingActivity],
) -> PaymentStart:
    """Новый платёж (своя pg_order_id на каждую попытку) и URL платёжной страницы."""
    payment = Payment(
        booking_id=booking.id,
        pg_order_id=uuid.uuid4().hex,
        amount=amount,
        commission_amount=rules.commission(amount, booking.commission_rate),
    )
    session.add(payment)
    await session.flush()
    for item in items:
        item.booking_id = booking.id
        item.payment_id = payment.id
        session.add(item)
    payment_url = await provider.create_payment(payment.pg_order_id, amount)
    await session.commit()
    await session.refresh(booking, ["payments", "activities"])
    return PaymentStart(
        booking=await booking_out(session, booking, me),
        payment=PaymentOut.model_validate(payment),
        payment_url=payment_url,
    )


def _check_dates(data: StayBookingCreate | VehicleBookingCreate) -> None:
    if data.start_date < rules.today_bishkek():
        raise bad_request("Дата начала в прошлом")


# --- Создание --------------------------------------------------------------------


@router.post("/stay", status_code=status.HTTP_201_CREATED)
async def create_stay(
    data: StayBookingCreate, me: CurrentUser, session: Session, provider: Provider
) -> PaymentStart:
    """Бронь проживания (можно сразу с активностями) и платёж к ней.

    Места заняты сразу; после оплаты бронь станет confirmed. Бронировать может только
    пользователь с одобренными документами (is_verified).
    """
    if not me.is_verified:
        raise forbidden("Бронировать можно после проверки документов")
    _check_dates(data)
    # Блокировки всегда в одном порядке: гость → хозяин. Параллельные брони ждут друг друга.
    await session.execute(select(User.id).where(User.id == me.id).with_for_update())
    row = (
        await session.execute(
            select(HostProfile)
            .join(User, User.id == HostProfile.user_id)
            .where(HostProfile.user_id == data.host_id, rules.host_visible())
            .with_for_update(of=HostProfile)
        )
    ).first()
    if row is None:
        raise not_found("Хозяин не найден")
    host = row[0]
    if host.user_id == me.id:
        raise bad_request("Нельзя забронировать у самого себя")
    await rules.check_stay_available(
        session, host, me.id, data.start_date, data.end_date, data.guests_count
    )

    nights = (data.end_date - data.start_date).days
    booking = Booking(
        type=BookingType.STAY,
        guest_id=me.id,
        host_id=host.user_id,
        start_date=data.start_date,
        end_date=data.end_date,
        guests_count=data.guests_count,
        unit_price=host.price_per_person,
        amount=host.price_per_person * data.guests_count * nights,
        commission_rate=Decimal(await rules.get_setting(session, "commission_rate")),
    )
    session.add(booking)
    await session.flush()
    items = await _choose_activities(session, booking, data.activities)
    total = booking.amount + sum((i.amount for i in items), Decimal(0))
    return await _start_payment(session, provider, me, booking, total, items)


@router.post("/vehicle", status_code=status.HTTP_201_CREATED)
async def create_vehicle_booking(
    data: VehicleBookingCreate, me: CurrentUser, session: Session, provider: Provider
) -> PaymentStart:
    """Аренда авто и платёж к ней. Нужны одобренные документы и водительское удостоверение.

    Время получения авто гость и владелец обсуждают в чате.
    """
    if not me.is_verified:
        raise forbidden("Бронировать можно после проверки документов")
    if not me.is_driver_license_verified:
        raise forbidden("Для аренды авто нужно подтверждённое водительское удостоверение")
    _check_dates(data)
    row = (
        await session.execute(
            select(Vehicle)
            .join(User, User.id == Vehicle.owner_id)
            .where(Vehicle.id == data.vehicle_id, rules.vehicle_visible())
            .with_for_update(of=Vehicle)
        )
    ).first()
    if row is None:
        raise not_found("Авто не найдено")
    vehicle = row[0]
    if vehicle.owner_id == me.id:
        raise bad_request("Нельзя арендовать своё авто")
    await rules.check_vehicle_available(session, vehicle.id, data.start_date, data.end_date)

    booking = Booking(
        type=BookingType.VEHICLE,
        guest_id=me.id,
        vehicle_id=vehicle.id,
        start_date=data.start_date,
        end_date=data.end_date,
        unit_price=vehicle.price_per_day,
        amount=vehicle.price_per_day * (data.end_date - data.start_date).days,
        commission_rate=Decimal(await rules.get_setting(session, "commission_rate")),
    )
    session.add(booking)
    await session.flush()
    return await _start_payment(session, provider, me, booking, booking.amount, [])


# --- Списки и карточка --------------------------------------------------------


@router.get("/my")
async def list_my_bookings(
    me: CurrentUser,
    session: Session,
    page: Pagination,
    booking_status: BookingStatus | None = None,
) -> Page[BookingOut]:
    """Брони, где я гость."""
    query = select(Booking).where(Booking.guest_id == me.id).order_by(Booking.id.desc())
    if booking_status is not None:
        query = query.where(Booking.status == booking_status)
    result = await paginate(session, query, page)
    result["items"] = [await booking_out(session, b, me) for b in result["items"]]
    return Page[BookingOut].model_validate(result)


@router.get("/incoming")
async def list_incoming_bookings(
    me: CurrentUser,
    session: Session,
    page: Pagination,
    booking_status: BookingStatus | None = None,
) -> Page[BookingOut]:
    """Оплаченные брони моего жилья и моих авто (неоплаченные хозяину не видны)."""
    my_vehicles = select(Vehicle.id).where(Vehicle.owner_id == me.id)
    query = (
        select(Booking)
        .where(
            or_(Booking.host_id == me.id, Booking.vehicle_id.in_(my_vehicles)),
            Booking.status != BookingStatus.PENDING_PAYMENT,
        )
        .order_by(Booking.start_date.desc())
    )
    if booking_status is not None:
        query = query.where(Booking.status == booking_status)
    result = await paginate(session, query, page)
    result["items"] = [await booking_out(session, b, me) for b in result["items"]]
    return Page[BookingOut].model_validate(result)


@router.get("/{booking_id}")
async def get_booking(booking_id: int, me: CurrentUser, session: Session) -> BookingOut:
    booking = await get_booking_for(session, me, booking_id)
    if booking.status == BookingStatus.PENDING_PAYMENT and me.id != booking.guest_id:
        raise not_found("Бронь не найдена")
    return await booking_out(session, booking, me)


# --- Действия --------------------------------------------------------------------


@router.post("/{booking_id}/activities", status_code=status.HTTP_201_CREATED)
async def add_activities(
    booking_id: int, data: AddActivitiesIn, me: CurrentUser, session: Session, provider: Provider
) -> PaymentStart:
    """Докупить активности к оплаченной брони проживания — отдельный платёж."""
    booking = await get_booking_for(session, me, booking_id)
    if booking.guest_id != me.id:
        raise forbidden("Докупить активности может только гость")
    if booking.type != BookingType.STAY or booking.status not in rules.PAID_STATUSES:
        raise conflict("Активности докупаются к оплаченной брони проживания")
    items = await _choose_activities(session, booking, data.activities)
    total = sum((i.amount for i in items), Decimal(0))
    return await _start_payment(session, provider, me, booking, total, items)


@router.delete("/{booking_id}", status_code=status.HTTP_204_NO_CONTENT)
async def abandon_payment(booking_id: int, me: CurrentUser, session: Session) -> None:
    """Отказ от оплаты: бронь в статусе pending_payment удаляется, места освобождаются."""
    booking = await get_booking_for(session, me, booking_id, lock=True)
    if booking.guest_id != me.id:
        raise forbidden("Удалить неоплаченную бронь может только гость")
    if booking.status != BookingStatus.PENDING_PAYMENT:
        raise conflict("Бронь уже оплачена — используйте отмену")
    for item in booking.activities:
        await session.delete(item)
    for payment in booking.payments:
        await session.delete(payment)
    await session.flush()
    await session.delete(booking)
    await session.commit()


@router.post("/{booking_id}/cancel")
async def cancel_booking(
    booking_id: int, data: CancelIn, me: CurrentUser, session: Session, provider: Provider
) -> BookingOut:
    """Отмена оплаченной брони гостем. Хозяин отменить не может (только через поддержку).

    Возврат за проживание / аренду — от времени заезда (13:00 дня начала, Asia/Bishkek):
    за 72 ч и больше — 100%, от 48 до 72 ч — 80%, меньше 48 ч — 60%.
    Активности возвращаются полностью. С момента заезда отмена невозможна.
    """
    booking = await get_booking_for(session, me, booking_id, lock=True)
    if booking.guest_id != me.id:
        raise forbidden("Хозяин не может отменить бронь — обратитесь в поддержку")
    if booking.status != BookingStatus.CONFIRMED:
        raise conflict("Эту бронь отменить нельзя")
    now = rules.now_bishkek()
    percent = rules.refund_percent(now, await rules.checkin_moment(session, booking.start_date))
    if percent is None:
        raise conflict("С момента заезда отменить бронь нельзя")

    total = Decimal(0)
    for payment in booking.payments:
        if payment.status != PaymentStatus.PAID:
            if payment.status == PaymentStatus.PENDING:
                payment.status = PaymentStatus.FAILED
            continue
        activities = sum(
            (a.amount for a in booking.activities if a.payment_id == payment.id), Decimal(0)
        )
        refund = rules.money((payment.amount - activities) * percent / 100) + activities
        await provider.refund(payment.pg_order_id, refund)
        payment.refunded_amount = refund
        # Удержанная часть уходит хозяину минус комиссия.
        payment.commission_amount = rules.commission(
            payment.amount - refund, booking.commission_rate
        )
        total += refund

    booking.status = BookingStatus.CANCELLED_BY_GUEST
    booking.cancelled_at = now
    booking.cancel_reason = data.reason
    booking.refund_amount = total
    notify(
        session,
        await provider_id(session, booking),
        "booking_cancelled",
        {"booking_id": booking.id, "by": "guest"},
    )
    await session.commit()
    await session.refresh(booking, ["payments", "activities"])
    return await booking_out(session, booking, me)


@router.post("/{booking_id}/acknowledge")
async def acknowledge(booking_id: int, me: CurrentUser, session: Session) -> BookingOut:
    """Кнопка хозяина «Вижу, жду гостя» — отметка, статус брони не меняется."""
    booking = await get_booking_for(session, me, booking_id)
    if booking.host_id != me.id:
        raise forbidden("Отметку ставит хозяин")
    if booking.status != BookingStatus.CONFIRMED:
        raise conflict("Отметка ставится на оплаченную бронь до заселения")
    if booking.host_acknowledged_at is None:
        booking.host_acknowledged_at = rules.now_bishkek()
        await session.commit()
    return await booking_out(session, booking, me)


@router.post("/{booking_id}/check-in")
async def check_in(
    booking_id: int, data: CheckInIn, me: CurrentUser, session: Session
) -> BookingOut:
    """Заселение / передача авто: хозяин (владелец авто) вводит PIN гостя.

    Возможно с дня начала брони до дня её окончания. Число попыток не ограничено.
    """
    booking = await get_booking_for(session, me, booking_id, lock=True)
    if me.id == booking.guest_id:
        raise forbidden("PIN вводит хозяин / владелец авто")
    if booking.status != BookingStatus.CONFIRMED:
        raise conflict("Бронь не ждёт заселения")
    if not booking.start_date <= rules.today_bishkek() < booking.end_date:
        raise conflict("Заселение возможно с дня начала до дня окончания брони")
    guest_pin = await session.scalar(select(User.checkin_pin).where(User.id == booking.guest_id))
    if data.pin != guest_pin:
        raise bad_request("Неверный PIN")
    booking.status = BookingStatus.IN_PROGRESS
    booking.checked_in_at = rules.now_bishkek()
    await session.commit()
    return await booking_out(session, booking, me)
