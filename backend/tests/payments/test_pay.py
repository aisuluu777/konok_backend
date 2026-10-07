"""Оплата брони (имитация): POST /bookings/{id}/pay.

R-30, R-31 — mock-провайдер: успех → платёж succeeded, бронь confirmed; отказ → failed, бронь hold;
R-32 — idempotency_key: повтор не создаёт второй платёж; R-33 — commission_amount =
round(amount × commission_rate брони, 2); R-06 — после оплаты гость видит адрес; R-10, R-11 —
занятия при оплате: снимок, людей не больше гостей; R-39 — уведомления гостю и хозяину / владельцу.
"""

import asyncio
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.bookings.models import BookingStatus
from app.modules.notifications.models import Notification
from app.modules.payments import provider as provider_module
from app.modules.payments.models import BookingActivity, Payment
from app.modules.payments.provider import MockPaymentProvider
from tests.activities.test_activities import activity_body, create_activity
from tests.admin.helpers import assert_error
from tests.bookings.test_stay_bookings import (
    book,
    expire_hold,
    guest_with_headers,
    make_host,
    set_status,
    stay_body,
)
from tests.bookings.test_vehicle_bookings import make_vehicle, rent, rent_body

PAYMENT_FIELDS = {
    "id",
    "booking_id",
    "status",
    "amount",
    "currency",
    "idempotency_key",
    "paid_at",
    "created_at",
}


@pytest.fixture
def failing_provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """R-31: провайдер, настроенный на отказ."""
    monkeypatch.setattr(provider_module, "_provider", MockPaymentProvider(fail=True))
    yield


async def pay(
    client: AsyncClient, headers: dict[str, str], booking_id: int, key: str = "key-1", **extra: Any
) -> Response:
    return await client.post(
        f"/bookings/{booking_id}/pay", headers=headers, json={"idempotency_key": key, **extra}
    )


async def stay_hold(
    db_session: AsyncSession, client: AsyncClient, *, price: str = "1500.00", **body: Any
) -> tuple[int, dict[str, str], dict[str, str], dict[str, Any]]:
    """Хозяин + гость с hold. Возвращает host_id, заголовки хозяина и гостя, бронь."""
    host, host_headers = await make_host(db_session, client, price=price)
    _, guest_headers = await guest_with_headers(db_session, client)
    booking = await book(client, guest_headers, stay_body(host.id, **body))
    return host.id, host_headers, guest_headers, booking


async def payments_of(db_session: AsyncSession, booking_id: int) -> list[Payment]:
    rows = await db_session.scalars(
        select(Payment).where(Payment.booking_id == booking_id).order_by(Payment.id)
    )
    return list(rows.all())


async def notifications_of(
    db_session: AsyncSession, user_id: int
) -> list[tuple[str, dict[str, Any]]]:
    rows = await db_session.scalars(
        select(Notification).where(Notification.user_id == user_id).order_by(Notification.id)
    )
    return [(n.type, n.payload) for n in rows.all()]


async def test_pay_requires_auth(client: AsyncClient) -> None:
    """Без токена → 401."""
    assert_error(await client.post("/bookings/1/pay", json={}), 401, "unauthorized")


# --- успех (R-31) ---


async def test_pay_confirms_stay(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-31, R-06: успех → платёж succeeded, бронь confirmed, гость видит адрес хозяина."""
    _, _, headers, booking = await stay_hold(db_session, client, start=1, end=3, guests_count=2)

    response = await pay(client, headers, booking["id"])

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["booking_status"] == "confirmed"
    assert set(data["payment"]) == PAYMENT_FIELDS
    assert data["payment"]["status"] == "succeeded"
    assert data["payment"]["amount"] == "6000.00"  # 1500 × 2 × 2
    assert data["payment"]["currency"] == "KGS"
    assert data["payment"]["paid_at"] is not None

    detail = (await client.get(f"/bookings/{booking['id']}", headers=headers)).json()
    assert detail["status"] == "confirmed"
    assert detail["confirmed_at"] is not None
    assert detail["host_address"] == "ул. Секретная, 7"

    [payment] = await payments_of(db_session, booking["id"])
    assert payment.provider == "mock"
    assert payment.provider_transaction_id
    assert payment.payout_id is None


async def test_pay_confirms_vehicle(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-31: аренда машины подтверждается так же."""
    _, _, vehicle_id = await make_vehicle(db_session, client, price="5000.00")
    _, headers = await guest_with_headers(db_session, client)
    booking = await rent(client, headers, rent_body(vehicle_id, 1, 3))

    response = await pay(client, headers, booking["id"])

    assert response.status_code == 200, response.text
    assert response.json()["booking_status"] == "confirmed"
    assert response.json()["payment"]["amount"] == "10000.00"


async def test_notifications_on_success(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-39: гостю — booking_confirmed, хозяину — new_booking; PIN в payload нет."""
    host_id, _, headers, booking = await stay_hold(db_session, client)
    guest_id = booking["guest_id"]
    await pay(client, headers, booking["id"])

    expected = {"booking_id": booking["id"]}
    assert await notifications_of(db_session, guest_id) == [("booking_confirmed", expected)]
    assert await notifications_of(db_session, host_id) == [("new_booking", expected)]


async def test_vehicle_owner_notified(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-39: владельцу машины — new_booking."""
    owner, _, vehicle_id = await make_vehicle(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    booking = await rent(client, headers, rent_body(vehicle_id))
    await pay(client, headers, booking["id"])
    assert await notifications_of(db_session, owner.id) == [
        ("new_booking", {"booking_id": booking["id"]})
    ]


# --- отказ (R-31) ---


@pytest.mark.usefixtures("failing_provider")
async def test_failed_payment_keeps_hold(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-31: отказ → платёж failed, бронь остаётся hold, уведомлений нет, адреса нет."""
    host_id, _, headers, booking = await stay_hold(db_session, client)

    response = await pay(client, headers, booking["id"])

    assert response.status_code == 200, response.text
    assert response.json()["booking_status"] == "hold"
    assert response.json()["payment"]["status"] == "failed"
    assert response.json()["payment"]["paid_at"] is None
    detail = (await client.get(f"/bookings/{booking['id']}", headers=headers)).json()
    assert detail["status"] == "hold"
    assert detail["host_address"] is None
    assert await notifications_of(db_session, host_id) == []


async def test_retry_after_failure(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-31, R-32: после отказа можно оплатить снова с новым ключом."""
    monkeypatch.setattr(provider_module, "_provider", MockPaymentProvider(fail=True))
    _, _, headers, booking = await stay_hold(db_session, client)
    assert (await pay(client, headers, booking["id"], "k1")).json()["booking_status"] == "hold"

    monkeypatch.setattr(provider_module, "_provider", MockPaymentProvider())
    response = await pay(client, headers, booking["id"], "k2")

    assert response.json()["booking_status"] == "confirmed"
    statuses = [p.status for p in await payments_of(db_session, booking["id"])]
    assert statuses == ["failed", "succeeded"]


# --- идемпотентность (R-32) ---


async def test_same_key_returns_same_payment(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-32: повтор с тем же ключом возвращает тот же платёж, второго не создаёт."""
    _, _, headers, booking = await stay_hold(db_session, client)
    first = await pay(client, headers, booking["id"], "same")
    second = await pay(client, headers, booking["id"], "same")

    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert len(await payments_of(db_session, booking["id"])) == 1


@pytest.mark.usefixtures("failing_provider")
async def test_same_key_after_failure(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-32: повтор ключа неудачного платежа возвращает тот же failed, провайдер не вызывается."""
    _, _, headers, booking = await stay_hold(db_session, client)
    first = await pay(client, headers, booking["id"], "k")
    second = await pay(client, headers, booking["id"], "k")
    assert second.json() == first.json()
    assert len(await payments_of(db_session, booking["id"])) == 1


async def test_key_of_other_booking(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-32: ключ, уже использованный для другой брони, → 409."""
    _, _, headers, booking = await stay_hold(db_session, client)
    _, _, other_headers, other = await stay_hold(db_session, client)
    await pay(client, headers, booking["id"], "shared")

    assert_error(await pay(client, other_headers, other["id"], "shared"), 409, "conflict")
    assert await payments_of(db_session, other["id"]) == []


async def test_parallel_same_key(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-32: параллельные запросы с одним ключом — один платёж, оба ответа одинаковы."""
    _, _, headers, booking = await stay_hold(db_session, client)

    responses = await asyncio.gather(
        *(pay(client, headers, booking["id"], "same") for _ in range(3))
    )

    assert [r.status_code for r in responses] == [200, 200, 200]
    assert len({r.json()["payment"]["id"] for r in responses}) == 1
    assert len(await payments_of(db_session, booking["id"])) == 1


async def test_parallel_different_keys(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-31, R-32: параллельные оплаты одной брони разными ключами — проходит одна."""
    _, _, headers, booking = await stay_hold(db_session, client)

    responses = await asyncio.gather(
        *(pay(client, headers, booking["id"], f"k{i}") for i in range(3))
    )

    assert sorted(r.status_code for r in responses) == [200, 409, 409]
    assert [p.status for p in await payments_of(db_session, booking["id"])] == ["succeeded"]


@pytest.mark.parametrize("body", [{}, {"idempotency_key": ""}, {"idempotency_key": "x" * 201}])
async def test_key_required(
    client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    """R-32: idempotency_key обязателен → иначе 422."""
    _, _, headers, booking = await stay_hold(db_session, client)
    response = await client.post(f"/bookings/{booking['id']}/pay", headers=headers, json=body)
    assert_error(response, 422, "validation_error")


# --- доступ и статус брони ---


async def test_only_guest_pays(client: AsyncClient, db_session: AsyncSession) -> None:
    """Оплатить может только гость брони: хозяину и чужому — 404."""
    _, host_headers, _, booking = await stay_hold(db_session, client)
    _, stranger = await guest_with_headers(db_session, client)
    for headers in (host_headers, stranger):
        assert_error(await pay(client, headers, booking["id"]), 404, "not_found")
    assert_error(await pay(client, stranger, 999999), 404, "not_found")
    assert await payments_of(db_session, booking["id"]) == []


@pytest.mark.parametrize(
    "status",
    [
        BookingStatus.CONFIRMED,
        BookingStatus.IN_PROGRESS,
        BookingStatus.COMPLETED,
        BookingStatus.EXPIRED,
        BookingStatus.CANCELLED_BY_GUEST,
        BookingStatus.NO_SHOW,
    ],
)
async def test_only_hold_can_be_paid(
    client: AsyncClient, db_session: AsyncSession, status: BookingStatus
) -> None:
    """R-31: оплатить можно только hold → иначе 409."""
    _, _, headers, booking = await stay_hold(db_session, client)
    await set_status(db_session, booking["id"], status)
    assert_error(await pay(client, headers, booking["id"]), 409, "conflict")


async def test_expired_hold_cannot_be_paid(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-20: hold с истёкшим сроком (ещё не переведённый в expired) не оплачивается → 409."""
    _, _, headers, booking = await stay_hold(db_session, client)
    await expire_hold(db_session, booking["id"])
    assert_error(await pay(client, headers, booking["id"]), 409, "conflict")
    assert await payments_of(db_session, booking["id"]) == []


# --- комиссия (R-33) ---


async def test_commission_rounding(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-33: 12.25 × 0.1 = 1.225 → 1.23 (половина — от нуля)."""
    _, _, headers, booking = await stay_hold(db_session, client, price="12.25", start=1, end=2)
    await pay(client, headers, booking["id"])
    [payment] = await payments_of(db_session, booking["id"])
    assert payment.amount == Decimal("12.25")
    assert payment.commission_amount == Decimal("1.23")


async def test_commission_uses_booking_rate(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-33: действует ставка, скопированная в бронь, а не текущая в settings."""
    _, _, headers, booking = await stay_hold(db_session, client, price="1000.00", start=1, end=2)
    await db_session.execute(
        text("UPDATE settings SET value = '0.2500' WHERE key = 'commission_rate'")
    )
    await db_session.commit()

    await pay(client, headers, booking["id"])

    [payment] = await payments_of(db_session, booking["id"])
    assert payment.commission_amount == Decimal("100.00")


# --- занятия при оплате (R-10, R-11) ---


async def test_pay_with_activities(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-10, R-11: занятия входят в платёж; в booking_activities — снимок названия и цены."""
    host_id, host_headers, headers, booking = await stay_hold(
        db_session, client, price="1000.00", start=1, end=2, guests_count=3
    )
    a = await create_activity(
        client, host_headers, activity_body(name="Конная прогулка", price_per_person="800.00")
    )
    b = await create_activity(
        client, host_headers, activity_body(name="Мастер-класс", price_per_person="250.50")
    )

    response = await pay(
        client,
        headers,
        booking["id"],
        activities=[
            {"activity_id": a["id"], "persons": 3},
            {"activity_id": b["id"], "persons": 1},
        ],
    )

    assert response.status_code == 200, response.text
    # 1000 × 3 гостя × 1 ночь + 800 × 3 + 250.50 × 1
    assert response.json()["payment"]["amount"] == "5650.50"
    rows = (
        await db_session.scalars(
            select(BookingActivity)
            .where(BookingActivity.booking_id == booking["id"])
            .order_by(BookingActivity.id)
        )
    ).all()
    [payment] = await payments_of(db_session, booking["id"])
    assert [(r.activity_name, r.persons, r.price_per_person, r.amount) for r in rows] == [
        ("Конная прогулка", 3, Decimal("800.00"), Decimal("2400.00")),
        ("Мастер-класс", 1, Decimal("250.50"), Decimal("250.50")),
    ]
    assert {r.payment_id for r in rows} == {payment.id}

    # R-10: изменение занятия не трогает купленный снимок.
    await client.patch(
        f"/activities/{a['id']}", headers=host_headers, json={"price_per_person": "1"}
    )
    await db_session.refresh(rows[0])
    assert rows[0].price_per_person == Decimal("800.00")


async def test_activity_persons_limit(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-11: людей в занятии не больше гостей брони → иначе 400, платежа нет."""
    _, host_headers, headers, booking = await stay_hold(db_session, client, guests_count=2)
    act = await create_activity(client, host_headers, activity_body())
    response = await pay(
        client, headers, booking["id"], activities=[{"activity_id": act["id"], "persons": 3}]
    )
    assert_error(response, 400, "domain_error")
    assert await payments_of(db_session, booking["id"]) == []


async def test_activity_must_be_hosts_and_active(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-10, R-11: чужое или скрытое занятие → 404."""
    _, host_headers, headers, booking = await stay_hold(db_session, client)
    _, other_host_headers = await make_host(db_session, client)
    foreign = await create_activity(client, other_host_headers, activity_body())
    hidden = await create_activity(client, host_headers, activity_body())
    await client.post(f"/activities/{hidden['id']}/deactivate", headers=host_headers)

    for activity_id in (foreign["id"], hidden["id"], 999999):
        response = await pay(
            client, headers, booking["id"], activities=[{"activity_id": activity_id, "persons": 1}]
        )
        assert_error(response, 404, "not_found")
    assert await payments_of(db_session, booking["id"]) == []


async def test_no_activities_for_vehicle(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-11: занятия покупаются только с проживанием → 400."""
    host, host_headers = await make_host(db_session, client)
    act = await create_activity(client, host_headers, activity_body())
    _, _, vehicle_id = await make_vehicle(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    booking = await rent(client, headers, rent_body(vehicle_id))
    response = await pay(
        client, headers, booking["id"], activities=[{"activity_id": act["id"], "persons": 1}]
    )
    assert_error(response, 400, "domain_error")


@pytest.mark.parametrize(
    "activities",
    [
        [{"activity_id": 1, "persons": 0}],
        [{"activity_id": 0, "persons": 1}],
        [{"activity_id": 1, "persons": 1}, {"activity_id": 1, "persons": 2}],
    ],
)
async def test_activities_invalid(
    client: AsyncClient, db_session: AsyncSession, activities: list[dict[str, int]]
) -> None:
    """Неверный список занятий (0 человек, неверный id, повтор) → 422."""
    _, _, headers, booking = await stay_hold(db_session, client)
    assert_error(
        await pay(client, headers, booking["id"], activities=activities), 422, "validation_error"
    )


async def test_parallel_same_key_two_bookings(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-32: один ключ параллельно на две брони — один платёж, второй запрос 409 (не 500)."""
    _, _, headers_a, booking_a = await stay_hold(db_session, client)
    _, _, headers_b, booking_b = await stay_hold(db_session, client)

    responses = await asyncio.gather(
        pay(client, headers_a, booking_a["id"], "shared"),
        pay(client, headers_b, booking_b["id"], "shared"),
    )

    assert sorted(r.status_code for r in responses) == [200, 409]
    count = await db_session.scalar(
        select(func.count()).select_from(Payment).where(Payment.idempotency_key == "shared")
    )
    assert count == 1


async def test_commission_not_below_amount(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-33: при ставке 0.9999 и сумме 1.00 комиссия округляется до суммы → 400, а не 500."""
    await db_session.execute(
        text("UPDATE settings SET value = '0.9999' WHERE key = 'commission_rate'")
    )
    await db_session.commit()
    _, _, headers, booking = await stay_hold(db_session, client, price="1.00", start=1, end=2)

    assert_error(await pay(client, headers, booking["id"]), 400, "domain_error")
    assert await payments_of(db_session, booking["id"]) == []
