"""Отзывы и рейтинг, экстренная отмена admin со скрытой оценкой 1, PIN-заселение."""

from datetime import date

from sqlalchemy import select

from app.admin import emergency_cancel
from app.models import BookingStatus, HostBlockedDate, VehicleBlockedDate
from tests.conftest import (
    SessionFactory,
    auth,
    confirmed_stay,
    day,
    make_host,
    make_user,
    make_vehicle,
    pay,
)


async def _review(client, author, url: str, rating: int):
    return await client.post(
        url, headers=auth(author), json={"rating": rating, "text": "Отлично", "language": "ru"}
    )


async def test_anyone_can_review_but_not_themselves(client):
    host = await make_host()
    response = await _review(client, await make_user(), f"/api/v1/hosts/{host.id}/reviews", 5)
    assert response.status_code == 201
    assert response.json()["text_ru"] == "Отлично"
    assert (await _review(client, host, f"/api/v1/hosts/{host.id}/reviews", 5)).status_code == 400

    owner, vehicle_id = await make_vehicle()
    url = f"/api/v1/vehicles/{vehicle_id}/reviews"
    assert (await _review(client, await make_user(), url, 4)).status_code == 201
    assert (await _review(client, owner, url, 5)).status_code == 400


async def test_no_rating_without_reviews(client):
    host = await make_host()
    profile = (await client.get(f"/api/v1/hosts/{host.id}")).json()
    assert (profile["rating"], profile["reviews_count"]) == (None, 0)

    # Скрытая оценка без отзывов рейтинг не показывает.
    cancelled = await confirmed_stay(client, await make_user(), host, 5, 6)
    async with SessionFactory() as session:
        await emergency_cancel(session, cancelled["booking"]["id"], host.id, reason_valid=False)
    profile = (await client.get(f"/api/v1/hosts/{host.id}")).json()
    assert (profile["rating"], profile["reviews_count"]) == (None, 0)


async def test_hidden_rating_one_after_invalid_admin_cancel(client):
    host = await make_host(capacity=4)
    await _review(client, await make_user(), f"/api/v1/hosts/{host.id}/reviews", 5)

    cancelled = await confirmed_stay(client, await make_user(), host, 5, 7)
    async with SessionFactory() as session:
        booking = await emergency_cancel(
            session, cancelled["booking"]["id"], admin_id=host.id, reason_valid=False
        )
        assert booking.status == BookingStatus.CANCELLED_BY_ADMIN
        assert booking.refund_amount == booking.amount  # гостю полный возврат
        blocked = set(
            await session.scalars(
                select(HostBlockedDate.date).where(HostBlockedDate.host_id == host.id)
            )
        )
    assert blocked == {date.fromisoformat(day(5)), date.fromisoformat(day(6))}

    # (5 + 1) / 2 = 3; счётчик отзывов и список отзывов не меняются.
    profile = (await client.get(f"/api/v1/hosts/{host.id}")).json()
    assert (profile["rating"], profile["reviews_count"]) == ("3.00", 1)
    assert (await client.get(f"/api/v1/hosts/{host.id}/reviews")).json()["total"] == 1


async def test_valid_admin_cancel_does_not_touch_rating(client):
    host = await make_host()
    await _review(client, await make_user(), f"/api/v1/hosts/{host.id}/reviews", 4)
    cancelled = await confirmed_stay(client, await make_user(), host, 5, 6)
    async with SessionFactory() as session:
        await emergency_cancel(session, cancelled["booking"]["id"], host.id, reason_valid=True)
        assert not list(await session.scalars(select(HostBlockedDate)))
    profile = (await client.get(f"/api/v1/hosts/{host.id}")).json()
    assert profile["rating"] == "4.00"


async def test_invalid_admin_cancel_of_vehicle(client):
    owner, vehicle_id = await make_vehicle()
    await _review(client, await make_user(), f"/api/v1/vehicles/{vehicle_id}/reviews", 5)
    driver = await make_user(license=True)
    created = (
        await client.post(
            "/api/v1/bookings/vehicle",
            headers=auth(driver),
            json={"vehicle_id": vehicle_id, "start_date": day(3), "end_date": day(5)},
        )
    ).json()
    assert await pay(client, driver, created["payment"]["id"]) == 200
    async with SessionFactory() as session:
        await emergency_cancel(session, created["booking"]["id"], owner.id, reason_valid=False)
        blocked = set(await session.scalars(select(VehicleBlockedDate.date)))
    assert blocked == {date.fromisoformat(day(3)), date.fromisoformat(day(4))}

    vehicle = (await client.get(f"/api/v1/vehicles/{vehicle_id}")).json()
    assert (vehicle["rating"], vehicle["reviews_count"]) == ("3.00", 1)
    rebook = await client.post(
        "/api/v1/bookings/vehicle",
        headers=auth(await make_user(license=True)),
        json={"vehicle_id": vehicle_id, "start_date": day(3), "end_date": day(4)},
    )
    assert rebook.status_code == 409  # закрытые даты


async def test_pin_check_in(client):
    host = await make_host()
    guest = await make_user()
    today = await confirmed_stay(client, guest, host, 0, 2)
    url = f"/api/v1/bookings/{today['booking']['id']}/check-in"

    wrong = "000000" if guest.checkin_pin != "000000" else "111111"
    assert (await client.post(url, headers=auth(host), json={"pin": wrong})).status_code == 400
    by_guest = await client.post(url, headers=auth(guest), json={"pin": guest.checkin_pin})
    assert by_guest.status_code == 403

    ok = await client.post(url, headers=auth(host), json={"pin": guest.checkin_pin})
    assert ok.status_code == 200
    assert ok.json()["status"] == "in_progress"


async def test_pin_check_in_not_before_start_date(client):
    host = await make_host()
    guest = await make_user()
    future = await confirmed_stay(client, guest, host, 3, 4)
    response = await client.post(
        f"/api/v1/bookings/{future['booking']['id']}/check-in",
        headers=auth(host),
        json={"pin": guest.checkin_pin},
    )
    assert response.status_code == 409
