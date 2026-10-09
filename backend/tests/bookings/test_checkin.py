"""Заселение по PIN: POST /bookings/{id}/checkin.

R-23 — хозяин / владелец машины вводит PIN гостя, confirmed → in_progress; неверный PIN
увеличивает счётчик без блокировки; R-35 — выплата хозяину при заселении (по аренде машины —
позже, при completed), докупка после заселения даёт отдельную выплату; R-39 — уведомления.
"""

import asyncio
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.bookings.models import BookingStatus
from app.modules.payments.models import Payment, Payout, PayoutStatus
from tests.activities.test_activities import activity_body, create_activity
from tests.admin.helpers import assert_error
from tests.bookings.test_stay_bookings import guest_with_headers, set_status
from tests.bookings.test_vehicle_bookings import make_vehicle, rent, rent_body
from tests.payments.test_add_activities import add
from tests.payments.test_pay import notifications_of, pay, payments_of, stay_hold

PIN = "123456"


async def checkin(
    client: AsyncClient, headers: dict[str, str], booking_id: int, pin: str = PIN
) -> Response:
    return await client.post(f"/bookings/{booking_id}/checkin", headers=headers, json={"pin": pin})


async def give_pin(db_session: AsyncSession, user_id: int, pin: str = PIN) -> None:
    await db_session.execute(
        text("UPDATE users SET checkin_pin = :pin WHERE id = :id"), {"pin": pin, "id": user_id}
    )
    await db_session.commit()


async def confirmed_stay(
    db_session: AsyncSession, client: AsyncClient, **body: Any
) -> tuple[int, dict[str, str], dict[str, str], dict[str, Any]]:
    """host_id, заголовки хозяина и гостя, оплаченная бронь; у гостя PIN = 123456.

    По умолчанию заезд сегодня — заселить можно сразу (R-23)."""
    host_id, host_headers, headers, booking = await stay_hold(
        db_session, client, **{"start": 0, **body}
    )
    assert (await pay(client, headers, booking["id"], f"pay-{booking['id']}")).status_code == 200
    await give_pin(db_session, booking["guest_id"])
    return host_id, host_headers, headers, booking


async def payouts_of(db_session: AsyncSession, booking_id: int) -> list[Payout]:
    rows = await db_session.scalars(
        select(Payout).where(Payout.booking_id == booking_id).order_by(Payout.id)
    )
    return list(rows.all())


async def attempts(db_session: AsyncSession, booking_id: int) -> int:
    value: int = await db_session.scalar(
        text("SELECT checkin_pin_attempts FROM bookings WHERE id = :id"), {"id": booking_id}
    )
    return value


async def test_requires_auth(client: AsyncClient) -> None:
    assert_error(await client.post("/bookings/1/checkin", json={}), 401, "unauthorized")


async def test_checkin_stay(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-23, R-35: верный PIN → in_progress, выплата хозяину = сумма − комиссия, уже paid."""
    host_id, host_headers, headers, booking = await confirmed_stay(
        db_session, client, price="1000.00", start=0, end=1
    )

    response = await checkin(client, host_headers, booking["id"])

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "in_progress"
    [payment] = await payments_of(db_session, booking["id"])
    [payout] = await payouts_of(db_session, booking["id"])
    assert payout.recipient_id == host_id
    assert payout.status == PayoutStatus.PAID
    assert payout.paid_at is not None
    assert payout.amount == payment.amount - payment.commission_amount == Decimal("900.00")
    await db_session.refresh(payment)
    assert payment.payout_id == payout.id
    detail = (await client.get(f"/bookings/{booking['id']}", headers=headers)).json()
    assert detail["status"] == "in_progress"
    assert detail["host_address"] is not None


async def test_checkin_notifications(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-39: гостю — checked_in, хозяину — payout_paid; PIN в payload нет."""
    host_id, host_headers, _, booking = await confirmed_stay(db_session, client)
    await checkin(client, host_headers, booking["id"])
    payload = {"booking_id": booking["id"]}
    assert ("checked_in", payload) in await notifications_of(db_session, booking["guest_id"])
    assert ("payout_paid", payload) in await notifications_of(db_session, host_id)


async def test_wrong_pin_counts_attempts(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-23: неверный PIN → 400, счётчик растёт, статуса и выплат нет, блокировки нет."""
    _, host_headers, _, booking = await confirmed_stay(db_session, client)
    for expected in (1, 2, 3, 4, 5, 6):
        assert_error(
            await checkin(client, host_headers, booking["id"], "000000"), 400, "domain_error"
        )
        assert await attempts(db_session, booking["id"]) == expected
    assert await payouts_of(db_session, booking["id"]) == []

    response = await checkin(client, host_headers, booking["id"])  # и после ошибок верный проходит

    assert response.status_code == 200
    assert response.json()["status"] == "in_progress"


async def test_guest_without_pin(client: AsyncClient, db_session: AsyncSession) -> None:
    """У гостя нет PIN → любой ввод неверен."""
    _, host_headers, _, booking = await confirmed_stay(db_session, client)
    await db_session.execute(text("UPDATE users SET checkin_pin = NULL"))
    await db_session.commit()
    assert_error(await checkin(client, host_headers, booking["id"]), 400, "domain_error")


async def test_only_host_can_check_in(client: AsyncClient, db_session: AsyncSession) -> None:
    """Гость и посторонний → 404, счётчик не меняется."""
    _, _, headers, booking = await confirmed_stay(db_session, client)
    _, stranger = await guest_with_headers(db_session, client)
    for who in (headers, stranger):
        assert_error(await checkin(client, who, booking["id"]), 404, "not_found")
    assert await attempts(db_session, booking["id"]) == 0


@pytest.mark.parametrize(
    "status",
    [
        BookingStatus.HOLD,
        BookingStatus.IN_PROGRESS,
        BookingStatus.COMPLETED,
        BookingStatus.CANCELLED_BY_GUEST,
        BookingStatus.NO_SHOW,
    ],
)
async def test_only_confirmed(
    client: AsyncClient, db_session: AsyncSession, status: BookingStatus
) -> None:
    """Заселять можно только confirmed → иначе 409, выплат нет."""
    _, host_headers, _, booking = await confirmed_stay(db_session, client)
    await set_status(db_session, booking["id"], status)
    assert_error(await checkin(client, host_headers, booking["id"]), 409, "conflict")
    assert await payouts_of(db_session, booking["id"]) == []


@pytest.mark.parametrize("pin", ["", "12345", "1234567", "12345a", " 123456"])
async def test_pin_format(client: AsyncClient, db_session: AsyncSession, pin: str) -> None:
    _, host_headers, _, booking = await confirmed_stay(db_session, client)
    assert_error(await checkin(client, host_headers, booking["id"], pin), 422, "validation_error")


async def test_payout_covers_paid_activities(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-35: выплата — по всем succeeded-платежам брони, включая докупку до заселения."""
    _, host_headers, headers, booking = await confirmed_stay(
        db_session, client, price="1000.00", start=0, end=1
    )
    act = await create_activity(client, host_headers, activity_body(price_per_person="500.00"))
    await add(client, headers, booking["id"], [{"activity_id": act["id"], "persons": 1}])

    await checkin(client, host_headers, booking["id"])

    [payout] = await payouts_of(db_session, booking["id"])
    assert payout.amount == Decimal("1350.00")  # (1000 + 500) − 10%
    payments = await payments_of(db_session, booking["id"])
    assert {p.payout_id for p in payments} == {payout.id}


async def test_late_activity_gets_separate_payout(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-35: докупка после заселения → отдельная выплата сразу после оплаты."""
    host_id, host_headers, headers, booking = await confirmed_stay(
        db_session, client, price="1000.00", start=0, end=1
    )
    await checkin(client, host_headers, booking["id"])
    act = await create_activity(client, host_headers, activity_body(price_per_person="500.00"))

    response = await add(client, headers, booking["id"], [{"activity_id": act["id"], "persons": 1}])

    assert response.status_code == 200
    first, late = await payouts_of(db_session, booking["id"])
    assert (first.amount, late.amount) == (Decimal("900.00"), Decimal("450.00"))
    assert late.recipient_id == host_id and late.status == PayoutStatus.PAID
    payments = await payments_of(db_session, booking["id"])
    assert [p.payout_id for p in payments] == [first.id, late.id]


async def test_failed_late_activity_no_payout(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ провайдера при докупке после заселения выплаты не создаёт."""
    from app.modules.payments import provider as provider_module
    from app.modules.payments.provider import MockPaymentProvider

    _, host_headers, headers, booking = await confirmed_stay(db_session, client)
    await checkin(client, host_headers, booking["id"])
    act = await create_activity(client, host_headers, activity_body())
    monkeypatch.setattr(provider_module, "_provider", MockPaymentProvider(fail=True))
    await add(client, headers, booking["id"], [{"activity_id": act["id"], "persons": 1}])
    assert len(await payouts_of(db_session, booking["id"])) == 1


async def test_checkin_vehicle(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-23, R-35: владелец машины вводит PIN → in_progress; выплата — только при completed."""
    owner, owner_headers, vehicle_id = await make_vehicle(db_session, client)
    guest, headers = await guest_with_headers(db_session, client)
    booking = await rent(client, headers, rent_body(vehicle_id, 0, 2))
    await pay(client, headers, booking["id"], "pay-vehicle")
    await give_pin(db_session, guest.id)

    assert_error(await checkin(client, headers, booking["id"]), 404, "not_found")
    response = await checkin(client, owner_headers, booking["id"])

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "in_progress"
    assert await payouts_of(db_session, booking["id"]) == []
    assert ("checked_in", {"booking_id": booking["id"]}) in await notifications_of(
        db_session, guest.id
    )
    payments = await db_session.scalars(select(Payment).where(Payment.booking_id == booking["id"]))
    assert all(p.payout_id is None for p in payments)


async def test_parallel_checkin_single_payout(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Два параллельных заселения: одно проходит, второе 409, выплата одна."""
    _, host_headers, _, booking = await confirmed_stay(db_session, client)
    responses = await asyncio.gather(
        checkin(client, host_headers, booking["id"]), checkin(client, host_headers, booking["id"])
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert len(await payouts_of(db_session, booking["id"])) == 1
