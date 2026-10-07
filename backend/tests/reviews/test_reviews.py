"""Отзывы: POST /bookings/{id}/reviews.

R-26 — только гость, только после completed, один отзыв на бронь; рейтинг 1–5; агрегат
rating_avg / reviews_count хозяина или машины; R-39 — уведомление тому, кого оценили.
"""

import asyncio
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.bookings.models import BookingStatus
from app.modules.reviews.models import Review
from tests.admin.helpers import assert_error
from tests.bookings.test_checkin import confirmed_stay
from tests.bookings.test_stay_bookings import (
    book,
    guest_with_headers,
    make_host,
    set_status,
    stay_body,
)
from tests.bookings.test_vehicle_bookings import make_vehicle, rent, rent_body
from tests.payments.test_pay import notifications_of, pay

REVIEW_FIELDS = {
    "id",
    "booking_id",
    "author_id",
    "target_user_id",
    "rating",
    "text",
    "status",
    "created_at",
}


async def review(
    client: AsyncClient, headers: dict[str, str], booking_id: int, **body: Any
) -> Response:
    return await client.post(f"/bookings/{booking_id}/reviews", headers=headers, json=body)


async def aggregate(db_session: AsyncSession, table: str, key: str, id_: int) -> tuple[Any, Any]:
    row = (
        await db_session.execute(
            text(f"SELECT rating_avg, reviews_count FROM {table} WHERE {key} = :id"), {"id": id_}
        )
    ).one()
    return row.rating_avg, row.reviews_count


async def completed_stay(
    db_session: AsyncSession, client: AsyncClient
) -> tuple[int, dict[str, str], dict[str, Any]]:
    host_id, _, headers, booking = await confirmed_stay(db_session, client)
    await set_status(db_session, booking["id"], BookingStatus.COMPLETED)
    return host_id, headers, booking


async def test_requires_auth(client: AsyncClient) -> None:
    assert_error(await client.post("/bookings/1/reviews", json={}), 401, "unauthorized")


async def test_review_stay(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-26: гость оставляет отзыв после completed; цель — хозяин; агрегат обновлён."""
    host_id, headers, booking = await completed_stay(db_session, client)

    response = await review(client, headers, booking["id"], rating=4, text="  Было здорово  ")

    assert response.status_code == 201, response.text
    data = response.json()
    assert set(data) == REVIEW_FIELDS
    assert (data["rating"], data["text"], data["status"]) == (4, "Было здорово", "published")
    assert (data["author_id"], data["target_user_id"]) == (booking["guest_id"], host_id)
    assert await aggregate(db_session, "host_profiles", "user_id", host_id) == (Decimal("4.00"), 1)


async def test_text_optional(client: AsyncClient, db_session: AsyncSession) -> None:
    _, headers, booking = await completed_stay(db_session, client)
    response = await review(client, headers, booking["id"], rating=5)
    assert response.status_code == 201
    assert response.json()["text"] is None


async def test_review_vehicle(client: AsyncClient, db_session: AsyncSession) -> None:
    """Отзыв об аренде: цель — владелец машины, агрегат — у машины."""
    owner, _, vehicle_id = await make_vehicle(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    booking = await rent(client, headers, rent_body(vehicle_id))
    await pay(client, headers, booking["id"], "pay-vehicle")
    await set_status(db_session, booking["id"], BookingStatus.COMPLETED)

    response = await review(client, headers, booking["id"], rating=3)

    assert response.status_code == 201, response.text
    assert response.json()["target_user_id"] == owner.id
    assert await aggregate(db_session, "vehicles", "id", vehicle_id) == (Decimal("3.00"), 1)


async def test_aggregate_averages_all_reviews(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """rating_avg — среднее по всем отзывам хозяина (5, 4, 4 → 4.33)."""
    host, host_headers = await make_host(db_session, client)
    ratings = (5, 4, 4)
    for i, rating in enumerate(ratings):
        _, headers = await guest_with_headers(db_session, client)
        booking = await book(client, headers, stay_body(host.id, start=1 + 3 * i, end=3 + 3 * i))
        await pay(client, headers, booking["id"], f"pay-{i}")
        await set_status(db_session, booking["id"], BookingStatus.COMPLETED)
        assert (await review(client, headers, booking["id"], rating=rating)).status_code == 201
    assert await aggregate(db_session, "host_profiles", "user_id", host.id) == (
        Decimal("4.33"),
        3,
    )


async def test_aggregate_ignores_vehicle_reviews_of_same_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Отзыв о машине не меняет рейтинг пользователя как хозяина."""
    host, _ = await make_host(db_session, client)
    _, _, vehicle_id = await make_vehicle(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    booking = await rent(client, headers, rent_body(vehicle_id))
    await pay(client, headers, booking["id"], "pay-vehicle")
    await set_status(db_session, booking["id"], BookingStatus.COMPLETED)
    await review(client, headers, booking["id"], rating=1)
    assert await aggregate(db_session, "host_profiles", "user_id", host.id) == (Decimal("0.00"), 0)


async def test_review_notifies_target(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-39: тому, кого оценили, — review_received; гостю ничего."""
    host_id, headers, booking = await completed_stay(db_session, client)
    response = await review(client, headers, booking["id"], rating=5)
    assert (
        "review_received",
        {"booking_id": booking["id"], "review_id": response.json()["id"], "rating": 5},
    ) in await notifications_of(db_session, host_id)
    assert all(
        t != "review_received" for t, _ in await notifications_of(db_session, booking["guest_id"])
    )


@pytest.mark.parametrize(
    "status",
    [
        BookingStatus.HOLD,
        BookingStatus.CONFIRMED,
        BookingStatus.IN_PROGRESS,
        BookingStatus.CANCELLED_BY_GUEST,
        BookingStatus.EXPIRED,
        BookingStatus.NO_SHOW,
    ],
)
async def test_only_after_completed(
    client: AsyncClient, db_session: AsyncSession, status: BookingStatus
) -> None:
    """R-26: пока бронь не completed → 409, отзыва и агрегата нет."""
    host_id, headers, booking = await completed_stay(db_session, client)
    await set_status(db_session, booking["id"], status)
    assert_error(await review(client, headers, booking["id"], rating=5), 409, "conflict")
    assert await aggregate(db_session, "host_profiles", "user_id", host_id) == (Decimal("0.00"), 0)


async def test_second_review_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-26: второй отзыв того же автора на ту же бронь → 409."""
    host_id, headers, booking = await completed_stay(db_session, client)
    await review(client, headers, booking["id"], rating=5)
    assert_error(await review(client, headers, booking["id"], rating=1), 409, "conflict")
    assert await aggregate(db_session, "host_profiles", "user_id", host_id) == (Decimal("5.00"), 1)


async def test_parallel_reviews_one_wins(client: AsyncClient, db_session: AsyncSession) -> None:
    _, headers, booking = await completed_stay(db_session, client)
    responses = await asyncio.gather(
        review(client, headers, booking["id"], rating=5),
        review(client, headers, booking["id"], rating=2),
    )
    assert sorted(r.status_code for r in responses) == [201, 409]
    rows = await db_session.scalars(select(Review).where(Review.booking_id == booking["id"]))
    assert len(rows.all()) == 1


async def test_parallel_reviews_of_same_host_keep_aggregate(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Два гостя оценивают одного хозяина одновременно — агрегат учитывает оба."""
    host, _ = await make_host(db_session, client)
    pairs = []
    for i in range(2):
        _, headers = await guest_with_headers(db_session, client)
        booking = await book(client, headers, stay_body(host.id, start=1 + 3 * i, end=3 + 3 * i))
        await pay(client, headers, booking["id"], f"pay-{i}")
        await set_status(db_session, booking["id"], BookingStatus.COMPLETED)
        pairs.append((headers, booking["id"], 5 - i * 2))  # 5 и 3
    responses = await asyncio.gather(*(review(client, h, b, rating=r) for h, b, r in pairs))
    assert [r.status_code for r in responses] == [201, 201]
    assert await aggregate(db_session, "host_profiles", "user_id", host.id) == (
        Decimal("4.00"),
        2,
    )


async def test_only_guest_of_booking(client: AsyncClient, db_session: AsyncSession) -> None:
    """Хозяин и посторонний → 404."""
    host_id, _, booking = await completed_stay(db_session, client)
    _, stranger = await guest_with_headers(db_session, client)
    assert_error(await review(client, stranger, booking["id"], rating=5), 404, "not_found")
    assert_error(await review(client, stranger, 999999, rating=5), 404, "not_found")


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"rating": 0},
        {"rating": 6},
        {"rating": 2.5},
        {"rating": "x"},
        {"rating": 3, "text": ""},
        {"rating": 3, "text": "x" * 2001},
    ],
)
async def test_invalid_body(
    client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    _, headers, booking = await completed_stay(db_session, client)
    assert_error(await review(client, headers, booking["id"], **body), 422, "validation_error")
