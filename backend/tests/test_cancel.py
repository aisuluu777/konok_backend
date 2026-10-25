"""Отмена: хозяин не отменяет, шкала возврата 100 / 80 / 60 от 13:00 дня заезда."""

from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest

from app import rules
from tests.conftest import auth, confirmed_stay, day, make_host, make_user, make_vehicle, pay


def at_checkin(start: datetime) -> datetime:
    return datetime.combine(start.date(), time(13, 0), tzinfo=rules.TZ)


@pytest.mark.parametrize(
    ("hours_before", "percent"),
    [(100, 100), (72, 100), (71.9, 80), (48, 80), (47.9, 60), (0.1, 60), (0, None), (-5, None)],
)
def test_refund_scale(hours_before, percent):
    checkin = datetime(2030, 6, 10, 13, 0, tzinfo=rules.TZ)
    now = checkin - timedelta(hours=hours_before)
    assert rules.refund_percent(now, checkin) == percent


async def test_host_cannot_cancel(client):
    host = await make_host()
    created = await confirmed_stay(client, await make_user(), host, 1, 2)
    response = await client.post(
        f"/api/v1/bookings/{created['booking']['id']}/cancel", headers=auth(host), json={}
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("hours_before", "refund"), [(80, "2000.00"), (60, "1600.00"), (5, "1200.00")]
)
async def test_guest_cancel_refund(client, monkeypatch, hours_before, refund):
    host = await make_host(price="1000.00")
    guest = await make_user()
    created = await confirmed_stay(client, guest, host, 10, 12)  # 2 ночи × 1000
    booking_id = created["booking"]["id"]

    start = datetime.fromisoformat(created["booking"]["start_date"])
    fake_now = at_checkin(start) - timedelta(hours=hours_before)
    monkeypatch.setattr(rules, "now_bishkek", lambda: fake_now)

    response = await client.post(
        f"/api/v1/bookings/{booking_id}/cancel", headers=auth(guest), json={"reason": "планы"}
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["status"] == "cancelled_by_guest"
    assert body["refund_amount"] == refund

    # Удержанная часть уходит хозяину минус 10% комиссии; места снова свободны.
    as_host = await client.get(f"/api/v1/bookings/{booking_id}", headers=auth(host))
    kept = Decimal("2000.00") - Decimal(refund)
    assert Decimal(as_host.json()["host_amount"]) == kept - rules.commission(kept, Decimal("0.10"))


async def test_no_cancel_after_checkin_time(client, monkeypatch):
    guest = await make_user()
    created = await confirmed_stay(client, guest, await make_host(), 3, 4)
    start = datetime.fromisoformat(created["booking"]["start_date"])
    monkeypatch.setattr(rules, "now_bishkek", lambda: at_checkin(start) + timedelta(minutes=1))
    response = await client.post(
        f"/api/v1/bookings/{created['booking']['id']}/cancel", headers=auth(guest), json={}
    )
    assert response.status_code == 409


async def test_cancel_frees_seats(client):
    host = await make_host(capacity=1)
    guest = await make_user()
    created = await confirmed_stay(client, guest, host, 10, 11)
    await client.post(
        f"/api/v1/bookings/{created['booking']['id']}/cancel", headers=auth(guest), json={}
    )
    # confirmed_stay сам проверяет, что оплата прошла (место снова свободно).
    await confirmed_stay(client, await make_user(), host, 10, 11)


async def test_activities_refunded_in_full(client, monkeypatch):
    host = await make_host(price="1000.00")
    guest = await make_user()
    activity = await client.post(
        "/api/v1/activities",
        headers=auth(host),
        json={"title_ru": "Конная прогулка", "price_per_person": "500.00"},
    )
    created = await client.post(
        "/api/v1/bookings/stay",
        headers=auth(guest),
        json={
            "host_id": host.id,
            "start_date": day(10),
            "end_date": day(11),
            "guests_count": 2,
            "activities": [{"activity_id": activity.json()["id"], "persons": 2}],
        },
    )
    body = created.json()
    assert body["payment"]["amount"] == "3000.00"  # 2 гостя × 1000 + 2 × 500
    assert await pay(client, guest, body["payment"]["id"]) == 200

    start = datetime.fromisoformat(body["booking"]["start_date"])
    monkeypatch.setattr(rules, "now_bishkek", lambda: at_checkin(start) - timedelta(hours=10))
    response = await client.post(
        f"/api/v1/bookings/{body['booking']['id']}/cancel", headers=auth(guest), json={}
    )
    # Проживание — 60% от 2000 = 1200, активности — полностью 1000.
    assert response.json()["refund_amount"] == "2200.00"


async def test_vehicle_cancel_uses_same_scale(client, monkeypatch):
    _, vehicle_id = await make_vehicle(price="3000.00")
    driver = await make_user(license=True)
    created = (
        await client.post(
            "/api/v1/bookings/vehicle",
            headers=auth(driver),
            json={"vehicle_id": vehicle_id, "start_date": day(10), "end_date": day(11)},
        )
    ).json()
    assert await pay(client, driver, created["payment"]["id"]) == 200
    start = datetime.fromisoformat(created["booking"]["start_date"])
    monkeypatch.setattr(rules, "now_bishkek", lambda: at_checkin(start) - timedelta(hours=50))
    response = await client.post(
        f"/api/v1/bookings/{created['booking']['id']}/cancel", headers=auth(driver), json={}
    )
    assert response.json()["refund_amount"] == "2400.00"  # 80%
