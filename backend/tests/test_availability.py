"""Места, пересечения броней, блокировка дат, право бронировать."""

import asyncio

from app.models import HostProfile
from tests.conftest import (
    SessionFactory,
    auth,
    book_stay,
    confirmed_stay,
    day,
    locality_id,
    make_host,
    make_user,
    make_vehicle,
    pay,
)


async def test_no_booking_over_free_seats(client):
    host = await make_host(capacity=3)
    await confirmed_stay(client, await make_user(), host, 1, 3, guests=2)

    late = await book_stay(client, await make_user(), host, 2, 4, guests=2)
    assert late["status"] == 409

    ok = await book_stay(client, await make_user(), host, 2, 4, guests=1)
    assert ok["status"] == 201


async def test_booking_in_payment_holds_seats(client):
    """Бронь занимает места сразу, ещё до оплаты; отказ от оплаты их освобождает."""
    host = await make_host(capacity=1)
    first = await make_user()
    in_payment = await book_stay(client, first, host, 1, 2)
    assert in_payment["status"] == 201
    assert in_payment["booking"]["status"] == "pending_payment"

    assert (await book_stay(client, await make_user(), host, 1, 2))["status"] == 409

    booking_id = in_payment["booking"]["id"]
    abandoned = await client.delete(f"/api/v1/bookings/{booking_id}", headers=auth(first))
    assert abandoned.status_code == 204
    assert (
        await client.get(f"/api/v1/bookings/{booking_id}", headers=auth(first))
    ).status_code == 404
    assert (await book_stay(client, await make_user(), host, 1, 2))["status"] == 201


async def test_parallel_bookings_for_last_seat(client):
    """Из одновременных броней последнего места проходит ровно одна (блокировка строки)."""
    host = await make_host(capacity=1)
    guests = [await make_user() for _ in range(5)]
    results = await asyncio.gather(*(book_stay(client, g, host, 1, 3) for g in guests))
    assert sorted(r["status"] for r in results) == [201, 409, 409, 409, 409]


async def test_paid_booking_cannot_be_deleted(client):
    guest = await make_user()
    created = await confirmed_stay(client, guest, await make_host(), 1, 2)
    response = await client.delete(
        f"/api/v1/bookings/{created['booking']['id']}", headers=auth(guest)
    )
    assert response.status_code == 409


async def test_guest_cannot_have_overlapping_stays(client):
    guest = await make_user()
    await confirmed_stay(client, guest, await make_host(), 1, 4)

    overlap = await book_stay(client, guest, await make_host(), 3, 5)
    assert overlap["status"] == 409

    # Выезд 4-го и заезд 4-го — не пересечение.
    next_stay = await book_stay(client, guest, await make_host(), 4, 6)
    assert next_stay["status"] == 201


async def test_host_blocks_only_free_dates(client):
    host = await make_host(capacity=4)
    await confirmed_stay(client, await make_user(), host, 2, 3)

    busy = await client.post(
        "/api/v1/hosts/me/blocked-dates", headers=auth(host), json={"dates": [day(2)]}
    )
    assert busy.status_code == 409

    free = await client.post(
        "/api/v1/hosts/me/blocked-dates", headers=auth(host), json={"dates": [day(5)]}
    )
    assert free.status_code == 200
    assert free.json()["dates"] == [day(5)]

    on_blocked = await book_stay(client, await make_user(), host, 4, 6)
    assert on_blocked["status"] == 409


async def test_only_verified_user_can_book(client):
    host = await make_host()
    not_verified = await book_stay(client, await make_user(verified=False), host, 1, 2)
    assert not_verified["status"] == 403


async def test_vehicle_rental_needs_driver_license(client):
    _, vehicle_id = await make_vehicle()
    body = {"vehicle_id": vehicle_id, "start_date": day(1), "end_date": day(3)}

    no_license = await client.post(
        "/api/v1/bookings/vehicle", headers=auth(await make_user()), json=body
    )
    assert no_license.status_code == 403

    driver = await make_user(license=True)
    created = await client.post("/api/v1/bookings/vehicle", headers=auth(driver), json=body)
    assert created.status_code == 201
    assert created.json()["booking"]["amount"] == "6000.00"
    assert await pay(client, driver, created.json()["payment"]["id"]) == 200

    # Те же дни этого авто уже заняты.
    other = await client.post(
        "/api/v1/bookings/vehicle", headers=auth(await make_user(license=True)), json=body
    )
    assert other.status_code == 409


async def test_unverified_host_hidden_and_not_bookable(client):
    host = await make_user(verified=False, name="host")
    async with SessionFactory() as session:
        session.add(
            HostProfile(
                user_id=host.id,
                locality_id=await locality_id(),
                address="—",
                capacity=2,
                price_per_person=100,
            )
        )
        await session.commit()
    catalog = await client.get("/api/v1/hosts")
    assert catalog.json()["total"] == 0
    assert (await book_stay(client, await make_user(), host, 1, 2))["status"] == 404
