"""Докупка занятий: POST /bookings/{id}/activities.

R-11 — только к stay в статусе confirmed / in_progress, людей не больше гостей; R-10 — снимок
названия и цены заморожен; R-32 — idempotency_key; R-33 — комиссия по ставке брони.
"""

import asyncio
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.bookings.models import BookingStatus
from app.modules.payments import provider as provider_module
from app.modules.payments.models import BookingActivity, PaymentStatus
from app.modules.payments.provider import MockPaymentProvider
from tests.activities.test_activities import activity_body, create_activity
from tests.admin.helpers import assert_error
from tests.bookings.test_stay_bookings import guest_with_headers, set_status
from tests.bookings.test_vehicle_bookings import make_vehicle, rent, rent_body
from tests.payments.test_pay import (
    PAYMENT_FIELDS,
    notifications_of,
    pay,
    payments_of,
    stay_hold,
)


async def add(
    client: AsyncClient,
    headers: dict[str, str],
    booking_id: int,
    activities: list[dict[str, int]],
    key: str = "add-1",
) -> Response:
    return await client.post(
        f"/bookings/{booking_id}/activities",
        headers=headers,
        json={"idempotency_key": key, "activities": activities},
    )


async def confirmed_stay(
    db_session: AsyncSession, client: AsyncClient, **body: Any
) -> tuple[dict[str, str], dict[str, str], dict[str, Any]]:
    """Хозяин, гость и оплаченная (confirmed) бронь."""
    _, host_headers, headers, booking = await stay_hold(db_session, client, **body)
    pay_key = f"pay-{booking['id']}"  # ключи уникальны на всю БД (R-32)
    assert (await pay(client, headers, booking["id"], pay_key)).status_code == 200
    return host_headers, headers, booking


async def test_requires_auth(client: AsyncClient) -> None:
    assert_error(await client.post("/bookings/1/activities", json={}), 401, "unauthorized")


@pytest.mark.parametrize("status", [BookingStatus.CONFIRMED, BookingStatus.IN_PROGRESS])
async def test_add_when_confirmed_or_in_progress(
    client: AsyncClient, db_session: AsyncSession, status: BookingStatus
) -> None:
    """R-11: докупка разрешена при confirmed и in_progress; статус брони не меняется."""
    host_headers, headers, booking = await confirmed_stay(
        db_session, client, price="1000.00", start=1, end=2, guests_count=3
    )
    await set_status(db_session, booking["id"], status)
    act = await create_activity(
        client, host_headers, activity_body(name="Конная прогулка", price_per_person="800.00")
    )

    response = await add(client, headers, booking["id"], [{"activity_id": act["id"], "persons": 2}])

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["booking_status"] == status.value
    assert set(data["payment"]) == PAYMENT_FIELDS
    assert data["payment"]["status"] == "succeeded"
    assert data["payment"]["amount"] == "1600.00"  # только занятия, без проживания
    first, second = await payments_of(db_session, booking["id"])
    assert second.id == data["payment"]["id"]
    # R-35: после заселения докупка сразу попадает в отдельную выплату.
    assert (second.payout_id is not None) == (status == BookingStatus.IN_PROGRESS)
    row = await db_session.scalar(
        select(BookingActivity).where(BookingActivity.payment_id == second.id)
    )
    assert row is not None
    assert (row.activity_name, row.persons, row.price_per_person, row.amount) == (
        "Конная прогулка",
        2,
        Decimal("800.00"),
        Decimal("1600.00"),
    )


@pytest.mark.parametrize(
    "status",
    [
        BookingStatus.HOLD,
        BookingStatus.COMPLETED,
        BookingStatus.CANCELLED_BY_GUEST,
        BookingStatus.EXPIRED,
    ],
)
async def test_add_rejected_in_other_statuses(
    client: AsyncClient, db_session: AsyncSession, status: BookingStatus
) -> None:
    """R-11: в остальных статусах → 409, платежа нет."""
    _, host_headers, headers, booking = await stay_hold(db_session, client)
    act = await create_activity(client, host_headers, activity_body())
    await set_status(db_session, booking["id"], status)

    response = await add(client, headers, booking["id"], [{"activity_id": act["id"], "persons": 1}])

    assert_error(response, 409, "conflict")
    assert await payments_of(db_session, booking["id"]) == []


async def test_snapshot_is_frozen(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-10: смена цены, названия и деактивация занятия не трогают купленный снимок."""
    host_headers, headers, booking = await confirmed_stay(db_session, client, guests_count=2)
    act = await create_activity(
        client, host_headers, activity_body(name="Йога", price_per_person="300.00")
    )
    await add(client, headers, booking["id"], [{"activity_id": act["id"], "persons": 2}])

    await client.patch(
        f"/activities/{act['id']}",
        headers=host_headers,
        json={"name": "Другое", "price_per_person": "1"},
    )
    await client.post(f"/activities/{act['id']}/deactivate", headers=host_headers)

    row = await db_session.scalar(
        select(BookingActivity).where(BookingActivity.booking_id == booking["id"])
    )
    assert row is not None
    await db_session.refresh(row)
    assert (row.activity_name, row.price_per_person, row.amount) == (
        "Йога",
        Decimal("300.00"),
        Decimal("600.00"),
    )


async def test_persons_limit(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-11: людей больше гостей брони → 400."""
    host_headers, headers, booking = await confirmed_stay(db_session, client, guests_count=2)
    act = await create_activity(client, host_headers, activity_body())
    response = await add(client, headers, booking["id"], [{"activity_id": act["id"], "persons": 3}])
    assert_error(response, 400, "domain_error")
    assert len(await payments_of(db_session, booking["id"])) == 1


async def test_activity_must_be_hosts_and_active(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Скрытое занятие или несуществующее → 404."""
    host_headers, headers, booking = await confirmed_stay(db_session, client)
    hidden = await create_activity(client, host_headers, activity_body())
    await client.post(f"/activities/{hidden['id']}/deactivate", headers=host_headers)
    for activity_id in (hidden["id"], 999999):
        response = await add(
            client, headers, booking["id"], [{"activity_id": activity_id, "persons": 1}]
        )
        assert_error(response, 404, "not_found")


async def test_only_guest_can_add(client: AsyncClient, db_session: AsyncSession) -> None:
    """Хозяин и посторонний получают 404, платежа нет."""
    host_headers, _, booking = await confirmed_stay(db_session, client)
    act = await create_activity(client, host_headers, activity_body())
    _, stranger = await guest_with_headers(db_session, client)
    for headers in (host_headers, stranger):
        response = await add(
            client, headers, booking["id"], [{"activity_id": act["id"], "persons": 1}]
        )
        assert_error(response, 404, "not_found")
    assert len(await payments_of(db_session, booking["id"])) == 1


async def test_vehicle_booking_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-11: к аренде машины занятия не докупаются → 400."""
    host_headers, _, _ = await confirmed_stay(db_session, client)
    act = await create_activity(client, host_headers, activity_body())
    _, _, vehicle_id = await make_vehicle(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    booking = await rent(client, headers, rent_body(vehicle_id))
    assert (await pay(client, headers, booking["id"], "pay-vehicle")).status_code == 200
    response = await add(client, headers, booking["id"], [{"activity_id": act["id"], "persons": 1}])
    assert_error(response, 400, "domain_error")


async def test_commission_uses_booking_rate(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-33: комиссия докупки — по ставке брони, а не текущей из settings."""
    host_headers, headers, booking = await confirmed_stay(db_session, client)
    act = await create_activity(client, host_headers, activity_body(price_per_person="1000.00"))
    await db_session.execute(
        text("UPDATE settings SET value = '0.9000' WHERE key = 'commission_rate'")
    )
    await db_session.commit()

    await add(client, headers, booking["id"], [{"activity_id": act["id"], "persons": 1}])

    _, second = await payments_of(db_session, booking["id"])
    assert second.commission_amount == Decimal("100.00")  # 10% из брони, не 90%


async def test_same_key_returns_same_payment(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-32: повтор с тем же ключом не создаёт второй платёж и вторые строки занятий."""
    host_headers, headers, booking = await confirmed_stay(db_session, client)
    act = await create_activity(client, host_headers, activity_body())
    body = [{"activity_id": act["id"], "persons": 1}]
    first = await add(client, headers, booking["id"], body)
    second = await add(client, headers, booking["id"], body)
    assert second.status_code == 200
    assert second.json()["payment"]["id"] == first.json()["payment"]["id"]
    assert len(await payments_of(db_session, booking["id"])) == 2
    rows = (
        await db_session.scalars(
            select(BookingActivity).where(BookingActivity.booking_id == booking["id"])
        )
    ).all()
    assert len(rows) == 1


async def test_key_of_other_booking(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-32: ключ, уже использованный в другой брони → 409."""
    host_headers, headers, booking = await confirmed_stay(db_session, client)
    act = await create_activity(client, host_headers, activity_body())
    other_host_headers, other_headers, other = await confirmed_stay(db_session, client)
    other_act = await create_activity(client, other_host_headers, activity_body())

    first = await add(
        client, headers, booking["id"], [{"activity_id": act["id"], "persons": 1}], key="x"
    )
    assert first.status_code == 200
    response = await add(
        client,
        other_headers,
        other["id"],
        [{"activity_id": other_act["id"], "persons": 1}],
        key="x",
    )
    assert_error(response, 409, "conflict")


async def test_parallel_same_key(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-32: два параллельных запроса с одним ключом → один платёж."""
    host_headers, headers, booking = await confirmed_stay(db_session, client)
    act = await create_activity(client, host_headers, activity_body())
    body = [{"activity_id": act["id"], "persons": 1}]
    responses = await asyncio.gather(
        add(client, headers, booking["id"], body), add(client, headers, booking["id"], body)
    )
    assert [r.status_code for r in responses] == [200, 200]
    assert len(await payments_of(db_session, booking["id"])) == 2


async def test_failed_payment(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ провайдера: платёж failed, бронь не меняется, можно повторить с новым ключом."""
    host_headers, headers, booking = await confirmed_stay(db_session, client)
    act = await create_activity(client, host_headers, activity_body())
    body = [{"activity_id": act["id"], "persons": 1}]
    monkeypatch.setattr(provider_module, "_provider", MockPaymentProvider(fail=True))

    response = await add(client, headers, booking["id"], body)

    assert response.status_code == 200
    assert response.json()["payment"]["status"] == "failed"
    assert response.json()["booking_status"] == "confirmed"
    assert (await payments_of(db_session, booking["id"]))[-1].status == PaymentStatus.FAILED


@pytest.mark.parametrize(
    "activities",
    [
        [],
        [{"activity_id": 1, "persons": 0}],
        [{"activity_id": 1, "persons": 1}, {"activity_id": 1, "persons": 2}],
    ],
)
async def test_invalid_list(
    client: AsyncClient, db_session: AsyncSession, activities: list[dict[str, int]]
) -> None:
    """Пустой список, 0 человек, повтор занятия → 422."""
    _, headers, booking = await confirmed_stay(db_session, client)
    assert_error(await add(client, headers, booking["id"], activities), 422, "validation_error")


async def test_notifications_on_success(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-39: успешная докупка → activities_paid гостю и хозяину."""
    host_headers, headers, booking = await confirmed_stay(db_session, client)
    act = await create_activity(client, host_headers, activity_body())
    host_id = booking["host_id"]

    response = await add(client, headers, booking["id"], [{"activity_id": act["id"], "persons": 1}])

    expected = (
        "activities_paid",
        {"booking_id": booking["id"], "payment_id": response.json()["payment"]["id"]},
    )
    assert expected in await notifications_of(db_session, booking["guest_id"])
    assert expected in await notifications_of(db_session, host_id)


async def test_no_notification_on_failed_payment(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ провайдера при докупке уведомлений не создаёт."""
    host_headers, headers, booking = await confirmed_stay(db_session, client)
    act = await create_activity(client, host_headers, activity_body())
    monkeypatch.setattr(provider_module, "_provider", MockPaymentProvider(fail=True))

    await add(client, headers, booking["id"], [{"activity_id": act["id"], "persons": 1}])

    for user_id in (booking["guest_id"], booking["host_id"]):
        assert all(t != "activities_paid" for t, _ in await notifications_of(db_session, user_id))
