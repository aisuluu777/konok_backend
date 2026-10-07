"""Управление доступностью хозяина: PUT/GET /hosts/me/blocked-dates, PUT /hosts/me/capacity.

R-13 — закрытый день недоступен для новых броней; R-14 — закрыть можно только целый день без
активных броней, прошедшие даты удаляются при записи; R-15 — вместимость нельзя опустить ниже
забронированного на будущую ночь; R-16 — всё под FOR UPDATE строки хозяина, гонка с бронью → одно.
"""

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.bookings.models import Booking, BookingStatus
from app.modules.hosts.models import HostProfile
from tests.admin.helpers import assert_error
from tests.bookings.test_stay_bookings import (
    book,
    day,
    expire_hold,
    guest_with_headers,
    make_host,
    set_status,
    stay_body,
)


async def put_blocked(client: AsyncClient, headers: dict[str, str], offsets: list[int]) -> Any:
    return await client.put(
        "/hosts/me/blocked-dates",
        headers=headers,
        json={"dates": [day(o).isoformat() for o in offsets]},
    )


async def stored_blocked(db_session: AsyncSession, host_id: int) -> list[str]:
    profile = await db_session.get(HostProfile, host_id, populate_existing=True)
    assert profile is not None
    return [d.isoformat() for d in profile.blocked_dates]


async def stored_capacity(db_session: AsyncSession, host_id: int) -> int:
    profile = await db_session.get(HostProfile, host_id, populate_existing=True)
    assert profile is not None
    return profile.capacity


# --- доступ ---


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/hosts/me/blocked-dates"),
        ("PUT", "/hosts/me/blocked-dates"),
        ("PUT", "/hosts/me/capacity"),
    ],
)
async def test_requires_auth(client: AsyncClient, method: str, path: str) -> None:
    """Без токена → 401."""
    assert_error(await client.request(method, path, json={}), 401, "unauthorized")


async def test_requires_host_profile(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-02: без профиля хозяина менять доступность нечего → 404."""
    _, headers = await guest_with_headers(db_session, client)
    assert_error(await client.get("/hosts/me/blocked-dates", headers=headers), 404, "not_found")
    assert_error(await put_blocked(client, headers, [1]), 404, "not_found")
    response = await client.put("/hosts/me/capacity", headers=headers, json={"capacity": 2})
    assert_error(response, 404, "not_found")


# --- закрытые дни (R-13, R-14) ---


async def test_block_dates(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-14: день без броней закрывается; список отсортирован, повторы схлопнуты."""
    host, headers = await make_host(db_session, client)

    response = await put_blocked(client, headers, [5, 2, 5])

    assert response.status_code == 200, response.text
    expected = [day(2).isoformat(), day(5).isoformat()]
    assert response.json() == {"blocked_dates": expected}
    assert await stored_blocked(db_session, host.id) == expected
    got = await client.get("/hosts/me/blocked-dates", headers=headers)
    assert got.json() == {"blocked_dates": expected}


async def test_blocked_day_rejects_booking(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-13: закрытый хозяином день отклоняет новую бронь."""
    host, headers = await make_host(db_session, client)
    assert (await put_blocked(client, headers, [2])).status_code == 200
    _, guest = await guest_with_headers(db_session, client)

    response = await client.post("/bookings/stay", headers=guest, json=stay_body(host.id, 1, 3))
    assert_error(response, 409, "conflict")


async def test_unblock_dates(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-14: снять закрытие — записать список без этого дня; пустой список открывает всё."""
    host, headers = await make_host(db_session, client)
    await put_blocked(client, headers, [2, 3])
    response = await put_blocked(client, headers, [])
    assert response.json() == {"blocked_dates": []}
    _, guest = await guest_with_headers(db_session, client)
    await book(client, guest, stay_body(host.id, 1, 4))


async def test_past_dates_removed(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-14: прошедшие даты удаляются при записи — и присланные, и уже сохранённые."""
    host, headers = await make_host(db_session, client)
    await db_session.execute(
        update(HostProfile)
        .where(HostProfile.user_id == host.id)
        .values(blocked_dates=[day(-10), day(4)])
    )
    await db_session.commit()

    response = await put_blocked(client, headers, [-1, 0, 4])

    assert response.status_code == 200, response.text
    assert response.json() == {"blocked_dates": [day(0).isoformat(), day(4).isoformat()]}


@pytest.mark.parametrize(
    "status", [BookingStatus.HOLD, BookingStatus.CONFIRMED, BookingStatus.IN_PROGRESS]
)
async def test_cannot_block_booked_day(
    client: AsyncClient, db_session: AsyncSession, status: BookingStatus
) -> None:
    """R-14: день с активной бронью закрыть нельзя → 409, список не меняется."""
    host, headers = await make_host(db_session, client)
    _, guest = await guest_with_headers(db_session, client)
    booking = await book(client, guest, stay_body(host.id, 2, 4))
    await set_status(db_session, booking["id"], status)

    response = await put_blocked(client, headers, [7, 2])  # 2 — день заезда, ночь брони

    assert_error(response, 409, "conflict")
    assert day(2).isoformat() in response.json()["error"]["message"]
    assert await stored_blocked(db_session, host.id) == []


async def test_checkout_day_can_be_blocked(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-14: день выезда — не ночь брони, его закрыть можно."""
    host, headers = await make_host(db_session, client)
    _, guest = await guest_with_headers(db_session, client)
    await book(client, guest, stay_body(host.id, 2, 4))
    assert (await put_blocked(client, headers, [4])).status_code == 200


@pytest.mark.parametrize(
    "status",
    [
        BookingStatus.CANCELLED_BY_GUEST,
        BookingStatus.EXPIRED,
        BookingStatus.COMPLETED,
        BookingStatus.NO_SHOW,
    ],
)
async def test_inactive_booking_does_not_prevent_block(
    client: AsyncClient, db_session: AsyncSession, status: BookingStatus
) -> None:
    """R-14: неактивные брони не мешают закрыть день."""
    host, headers = await make_host(db_session, client)
    _, guest = await guest_with_headers(db_session, client)
    booking = await book(client, guest, stay_body(host.id, 2, 4))
    await set_status(db_session, booking["id"], status)
    assert (await put_blocked(client, headers, [3])).status_code == 200


async def test_expired_hold_does_not_prevent_block(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-14, R-20: просроченный hold (ещё в статусе hold) день не держит."""
    host, headers = await make_host(db_session, client)
    _, guest = await guest_with_headers(db_session, client)
    booking = await book(client, guest, stay_body(host.id, 2, 4))
    await expire_hold(db_session, booking["id"])
    assert (await put_blocked(client, headers, [3])).status_code == 200


async def test_other_host_booking_does_not_prevent_block(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-14: учитываются только брони этого хозяина."""
    other, _ = await make_host(db_session, client)
    _, headers = await make_host(db_session, client)
    _, guest = await guest_with_headers(db_session, client)
    await book(client, guest, stay_body(other.id, 2, 4))
    assert (await put_blocked(client, headers, [3])).status_code == 200


@pytest.mark.parametrize("body", [{}, {"dates": None}, {"dates": ["x"]}, {"dates": "2030-01-01"}])
async def test_blocked_dates_invalid_body(
    client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    """Неверное тело → 422."""
    _, headers = await make_host(db_session, client)
    response = await client.put("/hosts/me/blocked-dates", headers=headers, json=body)
    assert_error(response, 422, "validation_error")


# --- вместимость (R-15) ---


async def test_change_capacity(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-15: без броней вместимость меняется в обе стороны."""
    _, headers = await make_host(db_session, client, capacity=4)
    for value in (6, 1):
        response = await client.put("/hosts/me/capacity", headers=headers, json={"capacity": value})
        assert response.status_code == 200, response.text
        assert response.json()["capacity"] == value


async def test_capacity_not_below_booked(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-15: нельзя опустить ниже забронированного на ночь; ровно до него — можно."""
    host, headers = await make_host(db_session, client, capacity=5)
    _, g1 = await guest_with_headers(db_session, client)
    _, g2 = await guest_with_headers(db_session, client)
    await book(client, g1, stay_body(host.id, 1, 3, guests_count=2))  # ночи 1, 2
    await book(client, g2, stay_body(host.id, 2, 4, guests_count=1))  # ночи 2, 3 → ночь 2: 3

    response = await client.put("/hosts/me/capacity", headers=headers, json={"capacity": 2})
    assert_error(response, 409, "conflict")
    assert await stored_capacity(db_session, host.id) == 5

    response = await client.put("/hosts/me/capacity", headers=headers, json={"capacity": 3})
    assert response.status_code == 200, response.text


async def test_capacity_checkout_night_not_counted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-15: «выезд 3-го / заезд 3-го» — брони не складываются, на ночь максимум 2."""
    host, headers = await make_host(db_session, client, capacity=4)
    _, g1 = await guest_with_headers(db_session, client)
    _, g2 = await guest_with_headers(db_session, client)
    await book(client, g1, stay_body(host.id, 1, 3, guests_count=2))
    await book(client, g2, stay_body(host.id, 3, 5, guests_count=2))
    response = await client.put("/hosts/me/capacity", headers=headers, json={"capacity": 2})
    assert response.status_code == 200, response.text


async def test_capacity_counts_tonight(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-15: сегодняшняя ночь — будущая, её брони учитываются."""
    host, headers = await make_host(db_session, client, capacity=3)
    _, guest = await guest_with_headers(db_session, client)
    await book(client, guest, stay_body(host.id, 0, 1, guests_count=3))
    response = await client.put("/hosts/me/capacity", headers=headers, json={"capacity": 2})
    assert_error(response, 409, "conflict")


async def test_capacity_ignores_past_nights(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-15: прошедшие ночи не учитываются — у брони, начавшейся раньше, только будущие ночи."""
    host, headers = await make_host(db_session, client, capacity=3)
    _, g1 = await guest_with_headers(db_session, client)
    _, g2 = await guest_with_headers(db_session, client)
    past = await book(client, g1, stay_body(host.id, 1, 2, guests_count=3))
    running = await book(client, g2, stay_body(host.id, 2, 4, guests_count=1))
    # Сдвигаем брони в прошлое: первая целиком прошла, вторая идёт (ночи -1 и сегодня).
    await db_session.execute(
        update(Booking)
        .where(Booking.id == past["id"])
        .values(start_date=day(-3), end_date=day(-2), status=BookingStatus.IN_PROGRESS)
    )
    await db_session.execute(
        update(Booking)
        .where(Booking.id == running["id"])
        .values(start_date=day(-1), end_date=day(1), status=BookingStatus.IN_PROGRESS)
    )
    await db_session.commit()

    response = await client.put("/hosts/me/capacity", headers=headers, json={"capacity": 1})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "status", [BookingStatus.CANCELLED_BY_GUEST, BookingStatus.EXPIRED, BookingStatus.NO_SHOW]
)
async def test_capacity_ignores_inactive(
    client: AsyncClient, db_session: AsyncSession, status: BookingStatus
) -> None:
    """R-15: неактивные брони не учитываются."""
    host, headers = await make_host(db_session, client, capacity=3)
    _, guest = await guest_with_headers(db_session, client)
    booking = await book(client, guest, stay_body(host.id, 1, 2, guests_count=3))
    await set_status(db_session, booking["id"], status)
    response = await client.put("/hosts/me/capacity", headers=headers, json={"capacity": 1})
    assert response.status_code == 200, response.text


async def test_capacity_ignores_expired_hold(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-15, R-20: просроченный hold места не держит."""
    host, headers = await make_host(db_session, client, capacity=3)
    _, guest = await guest_with_headers(db_session, client)
    booking = await book(client, guest, stay_body(host.id, 1, 2, guests_count=3))
    await expire_hold(db_session, booking["id"])
    response = await client.put("/hosts/me/capacity", headers=headers, json={"capacity": 1})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("value", [0, -1, None, 2**15, "x"])
async def test_capacity_invalid(client: AsyncClient, db_session: AsyncSession, value: Any) -> None:
    """Вместимость > 0 → иначе 422."""
    _, headers = await make_host(db_session, client)
    response = await client.put("/hosts/me/capacity", headers=headers, json={"capacity": value})
    assert_error(response, 422, "validation_error")


# --- R-16: конкурентность ---


async def test_parallel_block_and_booking(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-16: закрытие дня и бронь на этот день параллельно — проходит ровно одно."""
    for _ in range(5):
        host, headers = await make_host(db_session, client)
        _, guest = await guest_with_headers(db_session, client)

        block, booking = await asyncio.gather(
            put_blocked(client, headers, [2]),
            client.post("/bookings/stay", headers=guest, json=stay_body(host.id, 1, 3)),
        )

        assert sorted([block.status_code, booking.status_code]) in ([200, 409], [201, 409])
        blocked = await stored_blocked(db_session, host.id)
        assert (blocked == [day(2).isoformat()]) == (block.status_code == 200)


async def test_parallel_capacity_and_booking(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-16: снижение вместимости и бронь параллельно — мест не продано больше вместимости."""
    for _ in range(5):
        host, headers = await make_host(db_session, client, capacity=2)
        _, guest = await guest_with_headers(db_session, client)

        capacity, booking = await asyncio.gather(
            client.put("/hosts/me/capacity", headers=headers, json={"capacity": 1}),
            client.post("/bookings/stay", headers=guest, json=stay_body(host.id, guests_count=2)),
        )

        assert sorted([capacity.status_code, booking.status_code]) in ([200, 409], [201, 409])
        assert (await stored_capacity(db_session, host.id) == 1) == (capacity.status_code == 200)
