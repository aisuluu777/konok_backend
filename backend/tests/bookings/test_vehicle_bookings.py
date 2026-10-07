"""Аренда машины: POST /bookings/vehicle, GET /bookings/{id} для брони машины.

R-03 — арендовать можно только машину из каталога (опубликована, KYC владельца, не заблокирован);
R-05 — нельзя арендовать свою машину; R-17 — снимок цены за день; R-18 — аренда может пересекаться
со своим проживанием; R-19 — одну машину нельзя забронировать на пересекающиеся дни (EXCLUDE);
R-20 — просроченный hold освобождает машину.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.bookings.models import BookingStatus
from app.modules.users.models import KycStatus, User, UserStatus
from app.modules.vehicles.models import Vehicle
from tests.admin.helpers import assert_error
from tests.bookings.test_stay_bookings import (
    BOOKING_FIELDS,
    book,
    booking_rows,
    booking_status,
    day,
    expire_hold,
    guest_with_headers,
    make_host,
    set_status,
    stay_body,
)
from tests.hosts.test_hosts import first_region_id, set_kyc, user_with_headers
from tests.vehicles.test_vehicles import create_vehicle, owner_with_headers, vehicle_body


async def make_vehicle(
    db_session: AsyncSession, client: AsyncClient, *, price: str = "5000.00"
) -> tuple[User, dict[str, str], int]:
    """Машина в каталоге: опубликована, KYC владельца одобрен (R-03)."""
    owner, headers = await owner_with_headers(db_session, client)
    body = vehicle_body(await first_region_id(client), price_per_day=price)
    vehicle = await create_vehicle(client, headers, body)
    return owner, headers, int(vehicle["id"])


def rent_body(vehicle_id: int, start: int = 1, end: int = 3, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "vehicle_id": vehicle_id,
        "start_date": day(start).isoformat(),
        "end_date": day(end).isoformat(),
        "pickup_location": "Аэропорт Манас",
        "return_location": "Бишкек, центр",
    }
    body.update(overrides)
    return body


async def rent(
    client: AsyncClient, headers: dict[str, str], body: dict[str, Any]
) -> dict[str, Any]:
    response = await client.post("/bookings/vehicle", headers=headers, json=body)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


async def test_requires_auth(client: AsyncClient) -> None:
    """Без токена → 401."""
    assert_error(await client.post("/bookings/vehicle", json={}), 401, "unauthorized")


# --- создание ---


async def test_create_vehicle_hold(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-17, R-20: hold со снимком цены за день, суммой цена × дни и сроком hold_minutes."""
    _, _, vehicle_id = await make_vehicle(db_session, client, price="5000.00")
    guest, headers = await guest_with_headers(db_session, client)
    before = datetime.now(UTC)

    data = await rent(client, headers, rent_body(vehicle_id, 1, 4))

    assert set(data) == BOOKING_FIELDS
    assert data["type"] == "vehicle"
    assert data["status"] == "hold"
    assert data["guest_id"] == guest.id
    assert data["vehicle_id"] == vehicle_id
    assert data["host_id"] is None
    assert data["unit_price"] == "5000.00"
    assert data["base_amount"] == "15000.00"  # 5000 × 3 дня
    assert data["guests_count"] is None
    assert data["arrival_time"] is None
    assert data["pickup_location"] == "Аэропорт Манас"
    assert data["return_location"] == "Бишкек, центр"
    assert data["host_address"] is None
    expires = datetime.fromisoformat(data["hold_expires_at"])
    assert before + timedelta(minutes=14) < expires < before + timedelta(minutes=16)


async def test_price_change_does_not_affect_booking(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-17: изменение цены машины не меняет созданную бронь."""
    _, owner_headers, vehicle_id = await make_vehicle(db_session, client, price="5000.00")
    _, headers = await guest_with_headers(db_session, client)
    created = await rent(client, headers, rent_body(vehicle_id))
    response = await client.patch(
        f"/vehicles/{vehicle_id}", headers=owner_headers, json={"price_per_day": "100"}
    )
    assert response.status_code == 200, response.text

    data = (await client.get(f"/bookings/{created['id']}", headers=headers)).json()
    assert data["unit_price"] == "5000.00"
    assert data["base_amount"] == "10000.00"


async def test_cannot_rent_own_vehicle(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-05: свою машину арендовать нельзя → 400."""
    _, owner_headers, vehicle_id = await make_vehicle(db_session, client)
    response = await client.post(
        "/bookings/vehicle", headers=owner_headers, json=rent_body(vehicle_id)
    )
    assert_error(response, 400, "domain_error")
    assert await booking_rows(db_session) == 0


@pytest.mark.parametrize("kyc", [KycStatus.NOT_STARTED, KycStatus.PENDING, KycStatus.REJECTED])
async def test_guest_without_kyc_cannot_rent(
    client: AsyncClient, db_session: AsyncSession, kyc: KycStatus
) -> None:
    """R-01: без одобренного KYC арендовать машину нельзя → 403."""
    _, _, vehicle_id = await make_vehicle(db_session, client)
    guest, headers = await user_with_headers(db_session, client)
    await set_kyc(db_session, guest.id, kyc)
    response = await client.post("/bookings/vehicle", headers=headers, json=rent_body(vehicle_id))
    assert_error(response, 403, "permission_denied")
    assert await booking_rows(db_session) == 0


async def test_past_start_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    """Аренда с прошедшей даты → 400; с сегодняшней — можно."""
    _, _, vehicle_id = await make_vehicle(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    response = await client.post(
        "/bookings/vehicle", headers=headers, json=rent_body(vehicle_id, -1, 1)
    )
    assert_error(response, 400, "domain_error")
    await rent(client, headers, rent_body(vehicle_id, 0, 1))


async def test_amount_overflow_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-17: сумма, не помещающаяся в NUMERIC(12,2), → 400, а не 500."""
    _, _, vehicle_id = await make_vehicle(db_session, client, price="9999999999.99")
    _, headers = await guest_with_headers(db_session, client)
    response = await client.post(
        "/bookings/vehicle", headers=headers, json=rent_body(vehicle_id, 1, 3)
    )
    assert_error(response, 400, "domain_error")


# --- R-03: машина должна быть в каталоге ---


async def test_hidden_vehicle_not_rentable(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-03: неопубликованная машина, владелец без KYC или заблокированный → 404."""
    _, _, unpublished = await make_vehicle(db_session, client)
    no_kyc_owner, _, no_kyc = await make_vehicle(db_session, client)
    blocked_owner, _, blocked = await make_vehicle(db_session, client)
    await db_session.execute(
        update(Vehicle).where(Vehicle.id == unpublished).values(is_published=False)
    )
    await db_session.execute(
        update(User).where(User.id == blocked_owner.id).values(status=UserStatus.BLOCKED)
    )
    await db_session.commit()
    await set_kyc(db_session, no_kyc_owner.id, KycStatus.PENDING)
    _, headers = await guest_with_headers(db_session, client)

    for vehicle_id in (unpublished, no_kyc, blocked, 999999):
        response = await client.post(
            "/bookings/vehicle", headers=headers, json=rent_body(vehicle_id)
        )
        assert_error(response, 404, "not_found")


# --- валидация ---


@pytest.mark.parametrize(
    "overrides",
    [
        {"pickup_location": ""},
        {"pickup_location": None},
        {"return_location": "   "},
        {"return_location": "x" * 501},
        {"vehicle_id": 0},
        {"start_date": "x"},
    ],
)
async def test_invalid_body(
    client: AsyncClient, db_session: AsyncSession, overrides: dict[str, Any]
) -> None:
    """Неверное тело → 422."""
    _, _, vehicle_id = await make_vehicle(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    body = rent_body(vehicle_id)
    body.update(overrides)
    assert_error(
        await client.post("/bookings/vehicle", headers=headers, json=body), 422, "validation_error"
    )


@pytest.mark.parametrize(("start", "end"), [(3, 3), (3, 2)])
async def test_end_after_start(
    client: AsyncClient, db_session: AsyncSession, start: int, end: int
) -> None:
    """День возврата должен быть позже дня получения → иначе 422."""
    _, _, vehicle_id = await make_vehicle(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    response = await client.post(
        "/bookings/vehicle", headers=headers, json=rent_body(vehicle_id, start, end)
    )
    assert_error(response, 422, "validation_error")


# --- R-19 ---


async def test_overlapping_rental_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-19: пересекающиеся дни одной машины → 409."""
    _, _, vehicle_id = await make_vehicle(db_session, client)
    _, g1 = await guest_with_headers(db_session, client)
    _, g2 = await guest_with_headers(db_session, client)
    await rent(client, g1, rent_body(vehicle_id, 1, 4))

    response = await client.post("/bookings/vehicle", headers=g2, json=rent_body(vehicle_id, 3, 5))
    assert_error(response, 409, "conflict")
    assert await booking_rows(db_session) == 1


async def test_adjacent_rental_allowed(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-19: «возврат 4-го / получение 4-го» допустимо."""
    _, _, vehicle_id = await make_vehicle(db_session, client)
    _, g1 = await guest_with_headers(db_session, client)
    _, g2 = await guest_with_headers(db_session, client)
    await rent(client, g1, rent_body(vehicle_id, 1, 4))
    await rent(client, g2, rent_body(vehicle_id, 4, 6))


async def test_other_vehicle_same_dates(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-19: запрет только для одной машины; другую на те же дни — можно, и тому же гостю."""
    _, _, v1 = await make_vehicle(db_session, client)
    _, _, v2 = await make_vehicle(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    await rent(client, headers, rent_body(v1))
    await rent(client, headers, rent_body(v2))


@pytest.mark.parametrize("status", [BookingStatus.CONFIRMED, BookingStatus.IN_PROGRESS])
async def test_active_statuses_block_vehicle(
    client: AsyncClient, db_session: AsyncSession, status: BookingStatus
) -> None:
    """R-19: confirmed и in_progress держат машину."""
    _, _, vehicle_id = await make_vehicle(db_session, client)
    _, g1 = await guest_with_headers(db_session, client)
    _, g2 = await guest_with_headers(db_session, client)
    first = await rent(client, g1, rent_body(vehicle_id))
    await set_status(db_session, first["id"], status)
    response = await client.post("/bookings/vehicle", headers=g2, json=rent_body(vehicle_id))
    assert_error(response, 409, "conflict")


@pytest.mark.parametrize(
    "status",
    [
        BookingStatus.CANCELLED_BY_GUEST,
        BookingStatus.CANCELLED_BY_SUPPORT,
        BookingStatus.EXPIRED,
        BookingStatus.COMPLETED,
    ],
)
async def test_inactive_statuses_free_vehicle(
    client: AsyncClient, db_session: AsyncSession, status: BookingStatus
) -> None:
    """R-19: неактивные брони машину не держат."""
    _, _, vehicle_id = await make_vehicle(db_session, client)
    _, g1 = await guest_with_headers(db_session, client)
    _, g2 = await guest_with_headers(db_session, client)
    first = await rent(client, g1, rent_body(vehicle_id))
    await set_status(db_session, first["id"], status)
    await rent(client, g2, rent_body(vehicle_id))


# --- R-18: аренда и проживание ---


async def test_rental_may_overlap_own_stay(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-18: аренда машины может пересекаться с проживанием того же гостя — в обе стороны."""
    host, _ = await make_host(db_session, client)
    _, _, vehicle_id = await make_vehicle(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    await book(client, headers, stay_body(host.id, 1, 5))
    await rent(client, headers, rent_body(vehicle_id, 2, 4))

    host2, _ = await make_host(db_session, client)
    _, _, vehicle2 = await make_vehicle(db_session, client)
    _, headers2 = await guest_with_headers(db_session, client)
    await rent(client, headers2, rent_body(vehicle2, 1, 5))
    await book(client, headers2, stay_body(host2.id, 2, 4))


# --- R-20 ---


async def test_expired_hold_frees_vehicle(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-20: просроченный hold не держит машину и при новой брони становится expired."""
    _, _, vehicle_id = await make_vehicle(db_session, client)
    _, g1 = await guest_with_headers(db_session, client)
    _, g2 = await guest_with_headers(db_session, client)
    first = await rent(client, g1, rent_body(vehicle_id))
    await expire_hold(db_session, first["id"])

    await rent(client, g2, rent_body(vehicle_id))
    assert await booking_status(db_session, first["id"]) == "expired"


# --- конкурентность (R-19) ---


async def test_parallel_overlapping_rentals(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-19: пять параллельных пересекающихся броней одной машины — проходит ровно одна."""
    _, _, vehicle_id = await make_vehicle(db_session, client)
    guests = [await guest_with_headers(db_session, client) for _ in range(5)]

    responses = await asyncio.gather(
        *(
            client.post("/bookings/vehicle", headers=headers, json=rent_body(vehicle_id, 1, 4))
            for _, headers in guests
        )
    )

    assert sorted(r.status_code for r in responses) == [201, 409, 409, 409, 409]
    assert await booking_rows(db_session) == 1


async def test_parallel_rentals_over_expired_hold(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-19, R-20: параллельные брони поверх просроченного hold — тоже ровно одна."""
    _, _, vehicle_id = await make_vehicle(db_session, client)
    _, g0 = await guest_with_headers(db_session, client)
    stale = await rent(client, g0, rent_body(vehicle_id))
    await expire_hold(db_session, stale["id"])
    guests = [await guest_with_headers(db_session, client) for _ in range(3)]

    responses = await asyncio.gather(
        *(
            client.post("/bookings/vehicle", headers=headers, json=rent_body(vehicle_id))
            for _, headers in guests
        )
    )

    assert sorted(r.status_code for r in responses) == [201, 409, 409]
    assert await booking_status(db_session, stale["id"]) == "expired"


# --- GET /bookings/{id} ---


async def test_rental_visible_to_guest_and_owner(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Бронь машины видят гость и владелец машины; чужой — 404."""
    _, owner_headers, vehicle_id = await make_vehicle(db_session, client)
    _, guest_headers = await guest_with_headers(db_session, client)
    _, stranger = await guest_with_headers(db_session, client)
    created = await rent(client, guest_headers, rent_body(vehicle_id))
    url = f"/bookings/{created['id']}"

    for headers in (guest_headers, owner_headers):
        response = await client.get(url, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["vehicle_id"] == vehicle_id
        assert response.json()["host_address"] is None
    assert_error(await client.get(url, headers=stranger), 404, "not_found")


async def test_commission_rate_snapshot(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-33: commission_rate копируется из settings в бронь машины."""
    _, _, vehicle_id = await make_vehicle(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    created = await rent(client, headers, rent_body(vehicle_id))
    rate = await db_session.scalar(
        text("SELECT commission_rate FROM bookings WHERE id = :id"), {"id": created["id"]}
    )
    assert rate == Decimal("0.1000")
