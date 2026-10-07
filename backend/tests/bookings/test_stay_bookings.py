"""Бронь проживания: POST /bookings/stay, GET /bookings/{id}, GET /hosts/{id}/availability.

R-03 — бронь только у видимого хозяина; R-05 — нельзя у себя; R-06 — адрес только гостю после
оплаты; R-12 — свободные места по ночам; R-13 — заблокированная ночь; R-16 — FOR UPDATE хозяина,
на последнее место проходит ровно одна бронь; R-17 — снимок цены; R-18 — нет пересечений stay
одного гостя; R-20 — просроченный hold не держит места; R-21 — прибытие не раньше 13:00.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import today_bishkek
from app.modules.bookings.models import Booking, BookingStatus
from app.modules.hosts.models import HostProfile
from app.modules.users.models import KycStatus, User, UserStatus
from tests.admin.helpers import assert_error
from tests.hosts.test_hosts import (
    base_body,
    create_host,
    first_region_id,
    set_kyc,
    user_with_headers,
)

BOOKING_FIELDS = {
    "id",
    "type",
    "status",
    "guest_id",
    "host_id",
    "vehicle_id",
    "start_date",
    "end_date",
    "unit_price",
    "base_amount",
    "currency",
    "hold_expires_at",
    "confirmed_at",
    "guests_count",
    "arrival_time",
    "pickup_location",
    "return_location",
    "created_at",
    "host_address",
}


def day(offset: int) -> date:
    return today_bishkek() + timedelta(days=offset)


async def make_host(
    db_session: AsyncSession, client: AsyncClient, *, capacity: int = 4, price: str = "1500.00"
) -> tuple[User, dict[str, str]]:
    """Видимый хозяин: профиль опубликован, KYC одобрен (R-03)."""
    user, headers = await user_with_headers(db_session, client)
    body = base_body(await first_region_id(client), capacity=capacity, price_per_person_night=price)
    await create_host(client, headers, body)
    await set_kyc(db_session, user.id, KycStatus.APPROVED)
    return user, headers


async def guest_with_headers(
    db_session: AsyncSession, client: AsyncClient
) -> tuple[User, dict[str, str]]:
    """Гость с одобренным KYC — без него бронировать нельзя (R-01)."""
    user, headers = await user_with_headers(db_session, client)
    await set_kyc(db_session, user.id, KycStatus.APPROVED)
    return user, headers


def stay_body(host_id: int, start: int = 1, end: int = 3, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "host_id": host_id,
        "start_date": day(start).isoformat(),
        "end_date": day(end).isoformat(),
        "guests_count": 1,
        "arrival_time": "14:00",
    }
    body.update(overrides)
    return body


async def book(
    client: AsyncClient, headers: dict[str, str], body: dict[str, Any]
) -> dict[str, Any]:
    response = await client.post("/bookings/stay", headers=headers, json=body)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


async def expire_hold(db_session: AsyncSession, booking_id: int) -> None:
    await db_session.execute(
        update(Booking)
        .where(Booking.id == booking_id)
        .values(hold_expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    await db_session.commit()


async def set_status(db_session: AsyncSession, booking_id: int, status: BookingStatus) -> None:
    await db_session.execute(update(Booking).where(Booking.id == booking_id).values(status=status))
    await db_session.commit()


async def booking_status(db_session: AsyncSession, booking_id: int) -> str:
    value: str = await db_session.scalar(
        text("SELECT status::text FROM bookings WHERE id = :id"), {"id": booking_id}
    )
    return value


async def booking_rows(db_session: AsyncSession) -> int:
    count: int = await db_session.scalar(text("SELECT count(*) FROM bookings")) or 0
    return count


# --- без токена ---


@pytest.mark.parametrize(("method", "path"), [("POST", "/bookings/stay"), ("GET", "/bookings/1")])
async def test_bookings_require_auth(client: AsyncClient, method: str, path: str) -> None:
    """Брони без токена → 401."""
    assert_error(await client.request(method, path, json={}), 401, "unauthorized")


# --- создание hold ---


async def test_create_stay_hold(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-17, R-20: hold со снимком цены, суммой цена × гости × ночи и сроком hold_minutes."""
    host, _ = await make_host(db_session, client, price="1500.00")
    guest, headers = await guest_with_headers(db_session, client)
    before = datetime.now(UTC)

    data = await book(client, headers, stay_body(host.id, 1, 4, guests_count=2))

    assert set(data) == BOOKING_FIELDS
    assert data["type"] == "stay"
    assert data["status"] == "hold"
    assert data["guest_id"] == guest.id
    assert data["host_id"] == host.id
    assert data["vehicle_id"] is None
    assert data["unit_price"] == "1500.00"
    assert data["base_amount"] == "9000.00"  # 1500 × 2 гостя × 3 ночи
    assert data["currency"] == "KGS"
    assert data["guests_count"] == 2
    assert data["arrival_time"] == "14:00:00"
    assert data["host_address"] is None
    expires = datetime.fromisoformat(data["hold_expires_at"])
    assert before + timedelta(minutes=14) < expires < before + timedelta(minutes=16)


async def test_commission_rate_snapshot(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-33: commission_rate копируется из settings в бронь."""
    host, _ = await make_host(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    data = await book(client, headers, stay_body(host.id))
    rate = await db_session.scalar(
        text("SELECT commission_rate FROM bookings WHERE id = :id"), {"id": data["id"]}
    )
    assert rate == Decimal("0.1000")


async def test_price_change_does_not_affect_booking(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-17: изменение цены хозяина не меняет созданную бронь."""
    host, _ = await make_host(db_session, client, price="1500.00")
    _, headers = await guest_with_headers(db_session, client)
    created = await book(client, headers, stay_body(host.id))
    await db_session.execute(
        update(HostProfile)
        .where(HostProfile.user_id == host.id)
        .values(price_per_person_night=Decimal("9999"))
    )
    await db_session.commit()

    data = (await client.get(f"/bookings/{created['id']}", headers=headers)).json()
    assert data["unit_price"] == "1500.00"
    assert data["base_amount"] == created["base_amount"]


@pytest.mark.parametrize(
    "kyc",
    [KycStatus.NOT_STARTED, KycStatus.PENDING, KycStatus.REJECTED, KycStatus.RESUBMISSION],
)
async def test_guest_without_kyc_cannot_book(
    client: AsyncClient, db_session: AsyncSession, kyc: KycStatus
) -> None:
    """R-01: без одобренного KYC гость бронировать не может → 403 (решение #10 отменено)."""
    host, _ = await make_host(db_session, client)
    guest, headers = await user_with_headers(db_session, client)
    await set_kyc(db_session, guest.id, kyc)
    response = await client.post("/bookings/stay", headers=headers, json=stay_body(host.id))
    assert_error(response, 403, "permission_denied")
    assert await booking_rows(db_session) == 0


async def test_booking_today_allowed(client: AsyncClient, db_session: AsyncSession) -> None:
    """Заезд сегодня допустим, вчера — нет."""
    host, _ = await make_host(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    await book(client, headers, stay_body(host.id, 0, 1))
    response = await client.post("/bookings/stay", headers=headers, json=stay_body(host.id, -1, 0))
    assert_error(response, 400, "domain_error")


# --- R-05 ---


async def test_cannot_book_self(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-05: бронь у самого себя → 400."""
    host, headers = await make_host(db_session, client)
    response = await client.post("/bookings/stay", headers=headers, json=stay_body(host.id))
    assert_error(response, 400, "domain_error")
    assert await booking_rows(db_session) == 0


async def test_self_booking_db_check(db_session: AsyncSession, client: AsyncClient) -> None:
    """R-05: bookings_not_self_chk в БД не пропускает guest_id = host_id."""
    host, _ = await make_host(db_session, client)
    with pytest.raises(IntegrityError, match="bookings_not_self_chk"):
        await db_session.execute(
            text(
                "INSERT INTO bookings (type, guest_id, host_id, start_date, end_date, unit_price,"
                " base_amount, commission_rate, hold_expires_at, guests_count, arrival_time)"
                " VALUES ('stay', :u, :u, :s, :e, 100, 100, 0.1, now(), 1, '14:00')"
            ),
            {"u": host.id, "s": day(1), "e": day(2)},
        )
    await db_session.rollback()


# --- R-03: хозяин должен быть видим ---


@pytest.mark.parametrize("kyc", [KycStatus.NOT_STARTED, KycStatus.PENDING, KycStatus.REJECTED])
async def test_host_without_kyc_not_bookable(
    client: AsyncClient, db_session: AsyncSession, kyc: KycStatus
) -> None:
    """R-03: хозяин без одобренного KYC брони не принимает — 404."""
    host, _ = await make_host(db_session, client)
    await set_kyc(db_session, host.id, kyc)
    _, headers = await guest_with_headers(db_session, client)
    response = await client.post("/bookings/stay", headers=headers, json=stay_body(host.id))
    assert_error(response, 404, "not_found")


async def test_unpublished_or_blocked_host_not_bookable(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-03: неопубликованный и заблокированный хозяин брони не принимают."""
    unpublished, _ = await make_host(db_session, client)
    blocked, _ = await make_host(db_session, client)
    await db_session.execute(
        update(HostProfile).where(HostProfile.user_id == unpublished.id).values(is_published=False)
    )
    await db_session.execute(
        update(User).where(User.id == blocked.id).values(status=UserStatus.BLOCKED)
    )
    await db_session.commit()
    _, headers = await guest_with_headers(db_session, client)

    for host_id in (unpublished.id, blocked.id, 999999):
        response = await client.post("/bookings/stay", headers=headers, json=stay_body(host_id))
        assert_error(response, 404, "not_found")


# --- валидация тела ---


@pytest.mark.parametrize(
    "overrides",
    [
        {"guests_count": 0},
        {"guests_count": None},
        {"arrival_time": None},
        {"arrival_time": "25:00"},
        {"host_id": 0},
        {"start_date": "not-a-date"},
    ],
)
async def test_invalid_body(
    client: AsyncClient, db_session: AsyncSession, overrides: dict[str, Any]
) -> None:
    """Неверное тело → 422."""
    host, _ = await make_host(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    body = stay_body(host.id)
    body.update(overrides)
    response = await client.post("/bookings/stay", headers=headers, json=body)
    assert_error(response, 422, "validation_error")


@pytest.mark.parametrize(("start", "end"), [(3, 3), (3, 2)])
async def test_end_after_start(
    client: AsyncClient, db_session: AsyncSession, start: int, end: int
) -> None:
    """Дата выезда должна быть позже даты заезда → иначе 422."""
    host, _ = await make_host(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    response = await client.post(
        "/bookings/stay", headers=headers, json=stay_body(host.id, start, end)
    )
    assert_error(response, 422, "validation_error")


# --- R-21 ---


async def test_arrival_before_checkin_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-21: прибытие раньше 13:00 → 400; ровно 13:00 — можно."""
    host, _ = await make_host(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    response = await client.post(
        "/bookings/stay", headers=headers, json=stay_body(host.id, arrival_time="12:59")
    )
    assert_error(response, 400, "domain_error")
    await book(client, headers, stay_body(host.id, arrival_time="13:00"))


async def test_arrival_follows_setting(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-21: время заезда берётся из settings.checkin_time."""
    await db_session.execute(text("UPDATE settings SET value = '15:00' WHERE key = 'checkin_time'"))
    await db_session.commit()
    host, _ = await make_host(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    response = await client.post(
        "/bookings/stay", headers=headers, json=stay_body(host.id, arrival_time="14:00")
    )
    assert_error(response, 400, "domain_error")


# --- R-12: свободные места ---


async def test_exactly_last_seat(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-12: можно занять ровно последнее место, сверх вместимости — 409."""
    host, _ = await make_host(db_session, client, capacity=4)
    _, g1 = await guest_with_headers(db_session, client)
    _, g2 = await guest_with_headers(db_session, client)
    _, g3 = await guest_with_headers(db_session, client)

    await book(client, g1, stay_body(host.id, guests_count=3))
    await book(client, g2, stay_body(host.id, guests_count=1))
    response = await client.post("/bookings/stay", headers=g3, json=stay_body(host.id))
    assert_error(response, 409, "conflict")
    assert await booking_rows(db_session) == 2


async def test_too_many_guests(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-12: гостей больше вместимости → 409."""
    host, _ = await make_host(db_session, client, capacity=2)
    _, headers = await guest_with_headers(db_session, client)
    response = await client.post(
        "/bookings/stay", headers=headers, json=stay_body(host.id, guests_count=3)
    )
    assert_error(response, 409, "conflict")


async def test_seats_counted_per_night(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-12: занятость считается по ночам [start, end): ночь выезда свободна."""
    host, _ = await make_host(db_session, client, capacity=2)
    _, g1 = await guest_with_headers(db_session, client)
    _, g2 = await guest_with_headers(db_session, client)
    _, g3 = await guest_with_headers(db_session, client)
    await book(client, g1, stay_body(host.id, 1, 3, guests_count=2))  # ночи 1, 2

    response = await client.post(
        "/bookings/stay", headers=g2, json=stay_body(host.id, 2, 4)
    )  # ночь 2 занята
    assert_error(response, 409, "conflict")
    await book(client, g3, stay_body(host.id, 3, 5, guests_count=2))  # заезд в день выезда


@pytest.mark.parametrize(
    "status",
    [
        BookingStatus.CANCELLED_BY_GUEST,
        BookingStatus.CANCELLED_BY_SUPPORT,
        BookingStatus.EXPIRED,
        BookingStatus.COMPLETED,
        BookingStatus.NO_SHOW,
    ],
)
async def test_inactive_statuses_free_seats(
    client: AsyncClient, db_session: AsyncSession, status: BookingStatus
) -> None:
    """R-12: места держат только hold, confirmed, in_progress."""
    host, _ = await make_host(db_session, client, capacity=1)
    _, g1 = await guest_with_headers(db_session, client)
    _, g2 = await guest_with_headers(db_session, client)
    first = await book(client, g1, stay_body(host.id))
    await set_status(db_session, first["id"], status)
    await book(client, g2, stay_body(host.id))


@pytest.mark.parametrize("status", [BookingStatus.CONFIRMED, BookingStatus.IN_PROGRESS])
async def test_active_statuses_hold_seats(
    client: AsyncClient, db_session: AsyncSession, status: BookingStatus
) -> None:
    """R-12: confirmed и in_progress занимают места."""
    host, _ = await make_host(db_session, client, capacity=1)
    _, g1 = await guest_with_headers(db_session, client)
    _, g2 = await guest_with_headers(db_session, client)
    first = await book(client, g1, stay_body(host.id))
    await set_status(db_session, first["id"], status)
    response = await client.post("/bookings/stay", headers=g2, json=stay_body(host.id))
    assert_error(response, 409, "conflict")


# --- R-13 ---


async def test_blocked_night_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-13: ночь из blocked_dates недоступна; день выезда в blocked_dates не мешает."""
    host, _ = await make_host(db_session, client)
    await db_session.execute(
        update(HostProfile).where(HostProfile.user_id == host.id).values(blocked_dates=[day(2)])
    )
    await db_session.commit()
    _, headers = await guest_with_headers(db_session, client)

    response = await client.post("/bookings/stay", headers=headers, json=stay_body(host.id, 1, 3))
    assert_error(response, 409, "conflict")
    await book(client, headers, stay_body(host.id, 1, 2))  # 2-е — день выезда, не ночь


# --- R-18 ---


async def test_guest_overlap_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-18: пересекающиеся stay одного гостя у разных хозяев → 409."""
    h1, _ = await make_host(db_session, client)
    h2, _ = await make_host(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    await book(client, headers, stay_body(h1.id, 1, 5))

    response = await client.post("/bookings/stay", headers=headers, json=stay_body(h2.id, 4, 6))
    assert_error(response, 409, "conflict")
    assert await booking_rows(db_session) == 1


async def test_checkout_then_checkin_same_day(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-18: «выезд 5-го / заезд 5-го» допустимо."""
    h1, _ = await make_host(db_session, client)
    h2, _ = await make_host(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    await book(client, headers, stay_body(h1.id, 1, 5))
    await book(client, headers, stay_body(h2.id, 5, 7))


async def test_overlap_with_cancelled_allowed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-18: отменённая бронь не мешает новой на те же даты."""
    h1, _ = await make_host(db_session, client)
    h2, _ = await make_host(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    first = await book(client, headers, stay_body(h1.id))
    await set_status(db_session, first["id"], BookingStatus.CANCELLED_BY_GUEST)
    await book(client, headers, stay_body(h2.id))


# --- R-20 ---


async def test_expired_hold_frees_seat(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-20: просроченный hold не держит место и при новой брони становится expired."""
    host, _ = await make_host(db_session, client, capacity=1)
    _, g1 = await guest_with_headers(db_session, client)
    _, g2 = await guest_with_headers(db_session, client)
    first = await book(client, g1, stay_body(host.id))
    await expire_hold(db_session, first["id"])

    await book(client, g2, stay_body(host.id))
    assert await booking_status(db_session, first["id"]) == "expired"


async def test_guest_expired_hold_does_not_block_overlap(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-18, R-20: свой просроченный hold у другого хозяина не мешает новой брони на те же даты."""
    h1, _ = await make_host(db_session, client)
    h2, _ = await make_host(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    first = await book(client, headers, stay_body(h1.id))
    await expire_hold(db_session, first["id"])

    await book(client, headers, stay_body(h2.id))
    assert await booking_status(db_session, first["id"]) == "expired"


# --- R-16: конкурентность ---


async def test_parallel_last_seat(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-16: пять параллельных броней на последнее место — проходит ровно одна."""
    host, _ = await make_host(db_session, client, capacity=1)
    guests = [await guest_with_headers(db_session, client) for _ in range(5)]

    responses = await asyncio.gather(
        *(
            client.post("/bookings/stay", headers=headers, json=stay_body(host.id))
            for _, headers in guests
        )
    )

    assert sorted(r.status_code for r in responses) == [201, 409, 409, 409, 409]
    assert await booking_rows(db_session) == 1


async def test_parallel_guest_overlap(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-18: параллельные пересекающиеся stay одного гостя у разных хозяев — проходит одна."""
    hosts = [await make_host(db_session, client) for _ in range(3)]
    _, headers = await guest_with_headers(db_session, client)

    responses = await asyncio.gather(
        *(
            client.post("/bookings/stay", headers=headers, json=stay_body(host.id))
            for host, _ in hosts
        )
    )

    assert sorted(r.status_code for r in responses) == [201, 409, 409]
    assert await booking_rows(db_session) == 1


# --- GET /bookings/{id}, R-06 ---


async def test_booking_visible_to_guest_and_host(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Бронь видят гость и хозяин; чужой — 404."""
    host, host_headers = await make_host(db_session, client)
    _, guest_headers = await guest_with_headers(db_session, client)
    _, stranger = await guest_with_headers(db_session, client)
    created = await book(client, guest_headers, stay_body(host.id))
    url = f"/bookings/{created['id']}"

    for headers in (guest_headers, host_headers):
        response = await client.get(url, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["id"] == created["id"]
    assert_error(await client.get(url, headers=stranger), 404, "not_found")
    assert_error(await client.get("/bookings/999999", headers=guest_headers), 404, "not_found")


async def test_address_hidden_on_hold(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-06: на hold адрес хозяина гостю не виден."""
    host, _ = await make_host(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    created = await book(client, headers, stay_body(host.id))
    data = (await client.get(f"/bookings/{created['id']}", headers=headers)).json()
    assert data["host_address"] is None


@pytest.mark.parametrize(
    ("status", "visible"),
    [
        (BookingStatus.CONFIRMED, True),
        (BookingStatus.IN_PROGRESS, True),
        (BookingStatus.COMPLETED, True),
        (BookingStatus.EXPIRED, False),
        (BookingStatus.CANCELLED_BY_GUEST, False),
        (BookingStatus.NO_SHOW, False),
    ],
)
async def test_address_by_status(
    client: AsyncClient, db_session: AsyncSession, status: BookingStatus, visible: bool
) -> None:
    """R-06: адрес — только гостю при confirmed, in_progress, completed."""
    host, host_headers = await make_host(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    created = await book(client, headers, stay_body(host.id))
    await set_status(db_session, created["id"], status)

    data = (await client.get(f"/bookings/{created['id']}", headers=headers)).json()
    assert data["host_address"] == ("ул. Секретная, 7" if visible else None)
    host_view = (await client.get(f"/bookings/{created['id']}", headers=host_headers)).json()
    assert host_view["host_address"] is None


# --- GET /hosts/{id}/availability ---


async def test_availability(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-12, R-13, R-20: свободные места по ночам; просроченный hold не учитывается."""
    host, _ = await make_host(db_session, client, capacity=4)
    await db_session.execute(
        update(HostProfile).where(HostProfile.user_id == host.id).values(blocked_dates=[day(3)])
    )
    await db_session.commit()
    _, g1 = await guest_with_headers(db_session, client)
    _, g2 = await guest_with_headers(db_session, client)
    await book(client, g1, stay_body(host.id, 1, 3, guests_count=3))
    expired = await book(client, g2, stay_body(host.id, 2, 3, guests_count=1))
    await expire_hold(db_session, expired["id"])

    response = await client.get(
        f"/hosts/{host.id}/availability",
        params={"start_date": day(1).isoformat(), "end_date": day(4).isoformat()},
    )

    assert response.status_code == 200, response.text
    assert response.json() == [
        {"night": day(1).isoformat(), "free_seats": 1, "is_blocked": False},
        {"night": day(2).isoformat(), "free_seats": 1, "is_blocked": False},
        {"night": day(3).isoformat(), "free_seats": 4, "is_blocked": True},
    ]


async def test_availability_hidden_host(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-03: доступность невидимого хозяина → 404."""
    host, _ = await make_host(db_session, client)
    await set_kyc(db_session, host.id, KycStatus.PENDING)
    params = {"start_date": day(1).isoformat(), "end_date": day(2).isoformat()}
    assert_error(
        await client.get(f"/hosts/{host.id}/availability", params=params), 404, "not_found"
    )


@pytest.mark.parametrize(("start", "end"), [(2, 2), (3, 1), (0, 367)])
async def test_availability_invalid_period(
    client: AsyncClient, db_session: AsyncSession, start: int, end: int
) -> None:
    """Период: выезд позже заезда, не больше 366 ночей → иначе 422."""
    host, _ = await make_host(db_session, client)
    params = {"start_date": day(start).isoformat(), "end_date": day(end).isoformat()}
    assert_error(
        await client.get(f"/hosts/{host.id}/availability", params=params), 422, "validation_error"
    )


async def test_amount_overflow_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-17: сумма, не помещающаяся в NUMERIC(12,2), → 400, а не 500."""
    host, _ = await make_host(db_session, client, price="9999999999.99")
    _, headers = await guest_with_headers(db_session, client)
    response = await client.post(
        "/bookings/stay", headers=headers, json=stay_body(host.id, 1, 2, guests_count=2)
    )
    assert_error(response, 400, "domain_error")
    assert await booking_rows(db_session) == 0
