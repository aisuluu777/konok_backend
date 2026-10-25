"""Бизнес-правила: время, доступность, возвраты, комиссия, рейтинг, видимость в каталоге."""

from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import conflict
from app.models import (
    Booking,
    BookingStatus,
    BookingType,
    HostBlockedDate,
    HostProfile,
    Review,
    Setting,
    User,
    Vehicle,
    VehicleBlockedDate,
)

TZ = ZoneInfo("Asia/Bishkek")

# Брони, которые занимают места / даты: идёт оплата, оплачена, заселение.
HOLDING_STATUSES = (
    BookingStatus.PENDING_PAYMENT,
    BookingStatus.CONFIRMED,
    BookingStatus.IN_PROGRESS,
)
PAID_STATUSES = (BookingStatus.CONFIRMED, BookingStatus.IN_PROGRESS)


def now_bishkek() -> datetime:
    return datetime.now(TZ)


def today_bishkek() -> date:
    return now_bishkek().date()


async def get_setting(session: AsyncSession, key: str) -> str:
    value = await session.scalar(select(Setting.value).where(Setting.key == key))
    if value is None:
        raise RuntimeError(f"В таблице settings нет ключа {key}")
    return value


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def commission(amount: Decimal, rate: Decimal) -> Decimal:
    return money(amount * rate)


def days(start: date, end: date) -> list[date]:
    """Ночи проживания / дни аренды: [start, end)."""
    return [start + timedelta(days=i) for i in range((end - start).days)]


# --- Видимость в каталоге ----------------------------------------------------


def host_visible() -> ColumnElement[bool]:
    """Хозяин виден, если профиль опубликован, а владелец подтверждён и не заблокирован.

    Запрос должен содержать join(User, User.id == HostProfile.user_id).
    """
    return and_(HostProfile.is_published.is_(True), User.is_verified.is_(True), User.is_active)


def vehicle_visible() -> ColumnElement[bool]:
    """То же для авто; запрос должен содержать join(User, User.id == Vehicle.owner_id)."""
    return and_(Vehicle.is_published.is_(True), User.is_verified.is_(True), User.is_active)


# --- Доступность ---------------------------------------------------------------


def _overlaps(start: date, end: date) -> ColumnElement[bool]:
    return and_(Booking.start_date < end, Booking.end_date > start)


async def free_seats(
    session: AsyncSession, host: HostProfile, start: date, end: date
) -> dict[date, int]:
    """Свободные места на каждую ночь = вместимость − гости броней, которые держат места."""
    bookings = await session.scalars(
        select(Booking).where(
            Booking.host_id == host.user_id,
            Booking.status.in_(HOLDING_STATUSES),
            _overlaps(start, end),
        )
    )
    seats = {night: host.capacity for night in days(start, end)}
    for booking in bookings:
        for night in days(booking.start_date, booking.end_date):
            if night in seats:
                seats[night] -= booking.guests_count or 0
    return seats


async def blocked_dates(session: AsyncSession, host_id: int, start: date, end: date) -> set[date]:
    rows = await session.scalars(
        select(HostBlockedDate.date).where(
            HostBlockedDate.host_id == host_id,
            HostBlockedDate.date >= start,
            HostBlockedDate.date < end,
        )
    )
    return set(rows)


async def check_stay_available(
    session: AsyncSession,
    host: HostProfile,
    guest_id: int,
    start: date,
    end: date,
    guests_count: int,
) -> None:
    """Места на все ночи, ни одной заблокированной ночи, у гостя нет пересекающейся брони."""
    if await blocked_dates(session, host.user_id, start, end):
        raise conflict("Хозяин закрыл некоторые из этих дат")
    seats = await free_seats(session, host, start, end)
    if min(seats.values()) < guests_count:
        raise conflict("Недостаточно свободных мест на эти даты")
    overlapping = await session.scalar(
        select(Booking.id).where(
            Booking.guest_id == guest_id,
            Booking.type == BookingType.STAY,
            Booking.status.in_(HOLDING_STATUSES),
            _overlaps(start, end),
        )
    )
    if overlapping is not None:
        raise conflict("У вас уже есть проживание на эти даты")


async def busy_vehicle_days(
    session: AsyncSession, vehicle_id: int, start: date, end: date
) -> set[date]:
    """Дни, занятые бронями авто или закрытые admin."""
    bookings = await session.scalars(
        select(Booking).where(
            Booking.vehicle_id == vehicle_id,
            Booking.status.in_(HOLDING_STATUSES),
            _overlaps(start, end),
        )
    )
    blocked = await session.scalars(
        select(VehicleBlockedDate.date).where(
            VehicleBlockedDate.vehicle_id == vehicle_id,
            VehicleBlockedDate.date >= start,
            VehicleBlockedDate.date < end,
        )
    )
    return {day for b in bookings for day in days(b.start_date, b.end_date)} | set(blocked)


async def check_vehicle_available(
    session: AsyncSession, vehicle_id: int, start: date, end: date
) -> None:
    if await busy_vehicle_days(session, vehicle_id, start, end):
        raise conflict("Авто уже забронировано на эти даты")


# --- Отмена гостем -------------------------------------------------------------


async def checkin_moment(session: AsyncSession, start_date: date) -> datetime:
    """Момент заезда / получения авто: время заезда (13:00) в день начала, Asia/Bishkek."""
    checkin = time.fromisoformat(await get_setting(session, "checkin_time"))
    return datetime.combine(start_date, checkin, tzinfo=TZ)


def refund_percent(now: datetime, checkin: datetime) -> int | None:
    """Процент возврата гостю; None — отменять уже нельзя (наступило время заезда)."""
    if now >= checkin:
        return None
    hours_left = (checkin - now) / timedelta(hours=1)
    if hours_left >= 72:
        return 100
    if hours_left >= 48:
        return 80
    return 60


# --- Рейтинг -------------------------------------------------------------------


async def _rating(
    session: AsyncSession, review_target: ColumnElement[bool], booking_target: ColumnElement[bool]
) -> tuple[Decimal | None, int]:
    """Среднее звёзд отзывов плюс скрытые оценки 1 (отмена admin по неуважительной причине).

    Скрытые оценки входят в среднее, но не в счётчик отзывов. Без отзывов рейтинга нет.
    """
    stars, reviews_count = (
        await session.execute(
            select(func.coalesce(func.sum(Review.rating), 0), func.count(Review.id)).where(
                review_target
            )
        )
    ).one()
    if reviews_count == 0:
        return None, 0
    hidden_ones = await session.scalar(
        select(func.count(Booking.id)).where(
            booking_target,
            Booking.status == BookingStatus.CANCELLED_BY_ADMIN,
            Booking.admin_reason_valid.is_(False),
        )
    )
    hidden_ones = hidden_ones or 0
    return money(Decimal(stars + hidden_ones) / (reviews_count + hidden_ones)), reviews_count


async def host_rating(session: AsyncSession, host_id: int) -> tuple[Decimal | None, int]:
    return await _rating(session, Review.host_id == host_id, Booking.host_id == host_id)


async def vehicle_rating(session: AsyncSession, vehicle_id: int) -> tuple[Decimal | None, int]:
    return await _rating(session, Review.vehicle_id == vehicle_id, Booking.vehicle_id == vehicle_id)
