"""Закрытие брони модератором / админом: POST /bookings/{id}/complete и /no-show.

R-24 — no_show только stay из confirmed; R-35 — выплаты (vehicle при completed, no_show хозяину);
не раньше end_date (по Бишкеку); R-39 — уведомления.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import today_bishkek
from app.modules.bookings.models import BookingStatus
from app.modules.payments.models import PayoutStatus
from app.modules.users.models import UserRole
from tests.admin.helpers import assert_error
from tests.bookings.test_checkin import checkin, confirmed_stay, give_pin, payouts_of
from tests.bookings.test_stay_bookings import booking_status, guest_with_headers, set_status
from tests.bookings.test_vehicle_bookings import make_vehicle, rent, rent_body
from tests.kyc.test_kyc import user_with_headers
from tests.payments.test_pay import notifications_of, pay, payments_of


async def close(
    client: AsyncClient, headers: dict[str, str], booking_id: int, action: str
) -> Response:
    return await client.post(f"/bookings/{booking_id}/{action}", headers=headers)


async def end_stay_period(db_session: AsyncSession, booking_id: int, days_ago: int = 0) -> None:
    """Сдвигает бронь в прошлое: end_date = сегодня - days_ago, длительность та же (base_amount)."""
    end = today_bishkek() - timedelta(days=days_ago)
    await db_session.execute(
        text(
            "UPDATE bookings SET start_date = CAST(:e AS date) - (end_date - start_date), "
            "end_date = CAST(:e AS date) WHERE id = :id"
        ),
        {"e": end, "id": booking_id},
    )
    await db_session.commit()


async def staff(
    db_session: AsyncSession, client: AsyncClient, role: UserRole = UserRole.MODERATOR
) -> dict[str, str]:
    _, headers = await user_with_headers(db_session, client, role)
    return headers


@pytest.mark.parametrize("action", ["complete", "no-show"])
async def test_requires_auth_and_staff(
    client: AsyncClient, db_session: AsyncSession, action: str
) -> None:
    assert_error(await client.post(f"/bookings/1/{action}"), 401, "unauthorized")
    _, headers = await guest_with_headers(db_session, client)
    assert_error(await close(client, headers, 1, action), 403, "permission_denied")


@pytest.mark.parametrize("role", [UserRole.MODERATOR, UserRole.ADMIN])
async def test_complete_stay(client: AsyncClient, db_session: AsyncSession, role: UserRole) -> None:
    """in_progress → completed после end_date; новой выплаты нет (она была при заселении)."""
    _, host_headers, _, booking = await confirmed_stay(db_session, client)
    await checkin(client, host_headers, booking["id"])
    await end_stay_period(db_session, booking["id"])

    response = await close(client, await staff(db_session, client, role), booking["id"], "complete")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    assert len(await payouts_of(db_session, booking["id"])) == 1
    completed_at = await db_session.scalar(
        text("SELECT completed_at FROM bookings WHERE id = :id"), {"id": booking["id"]}
    )
    assert completed_at is not None


async def test_complete_stay_before_end_date(client: AsyncClient, db_session: AsyncSession) -> None:
    """До end_date закрыть нельзя → 409; в день выезда (end_date = сегодня) — можно."""
    _, host_headers, _, booking = await confirmed_stay(db_session, client, start=1, end=3)
    await checkin(client, host_headers, booking["id"])
    mod = await staff(db_session, client)

    assert_error(await close(client, mod, booking["id"], "complete"), 409, "conflict")
    assert await booking_status(db_session, booking["id"]) == "in_progress"

    await end_stay_period(db_session, booking["id"])
    assert (await close(client, mod, booking["id"], "complete")).status_code == 200


@pytest.mark.parametrize(
    "status",
    [
        BookingStatus.HOLD,
        BookingStatus.CONFIRMED,
        BookingStatus.COMPLETED,
        BookingStatus.CANCELLED_BY_GUEST,
        BookingStatus.EXPIRED,
        BookingStatus.NO_SHOW,
    ],
)
async def test_complete_stay_only_from_in_progress(
    client: AsyncClient, db_session: AsyncSession, status: BookingStatus
) -> None:
    _, _, _, booking = await confirmed_stay(db_session, client)
    await set_status(db_session, booking["id"], status)
    await end_stay_period(db_session, booking["id"], 1)
    mod = await staff(db_session, client)
    assert_error(await close(client, mod, booking["id"], "complete"), 409, "conflict")


async def test_no_show(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-24, R-35: confirmed stay после end_date → no_show, выплата хозяину, уведомления."""
    host_id, _, _, booking = await confirmed_stay(
        db_session, client, price="1000.00", start=1, end=2
    )
    await end_stay_period(db_session, booking["id"], 1)

    response = await close(client, await staff(db_session, client), booking["id"], "no-show")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "no_show"
    [payout] = await payouts_of(db_session, booking["id"])
    assert (payout.recipient_id, payout.amount, payout.status) == (
        host_id,
        Decimal("900.00"),
        PayoutStatus.PAID,
    )
    [payment] = await payments_of(db_session, booking["id"])
    assert payment.payout_id == payout.id
    payload = {"booking_id": booking["id"]}
    assert ("no_show", payload) in await notifications_of(db_session, booking["guest_id"])
    host_notes = await notifications_of(db_session, host_id)
    assert ("no_show", payload) in host_notes and ("payout_paid", payload) in host_notes


async def test_no_show_before_end_date(client: AsyncClient, db_session: AsyncSession) -> None:
    _, _, _, booking = await confirmed_stay(db_session, client, start=1, end=3)
    mod = await staff(db_session, client)
    assert_error(await close(client, mod, booking["id"], "no-show"), 409, "conflict")
    assert await payouts_of(db_session, booking["id"]) == []


@pytest.mark.parametrize(
    "status", [BookingStatus.HOLD, BookingStatus.IN_PROGRESS, BookingStatus.COMPLETED]
)
async def test_no_show_only_from_confirmed(
    client: AsyncClient, db_session: AsyncSession, status: BookingStatus
) -> None:
    _, _, _, booking = await confirmed_stay(db_session, client)
    await set_status(db_session, booking["id"], status)
    await end_stay_period(db_session, booking["id"], 1)
    mod = await staff(db_session, client)
    assert_error(await close(client, mod, booking["id"], "no-show"), 409, "conflict")


async def test_unknown_booking(client: AsyncClient, db_session: AsyncSession) -> None:
    mod = await staff(db_session, client)
    for action in ("complete", "no-show"):
        assert_error(await close(client, mod, 999999, action), 404, "not_found")


@pytest.mark.parametrize("start_status", [BookingStatus.CONFIRMED, BookingStatus.IN_PROGRESS])
async def test_complete_vehicle_pays_owner(
    client: AsyncClient, db_session: AsyncSession, start_status: BookingStatus
) -> None:
    """R-35: аренда машины из confirmed / in_progress → completed, выплата владельцу."""
    owner, owner_headers, vehicle_id = await make_vehicle(db_session, client, price="5000.00")
    guest, headers = await guest_with_headers(db_session, client)
    booking = await rent(client, headers, rent_body(vehicle_id, 1, 3))
    await pay(client, headers, booking["id"], "pay-vehicle")
    await give_pin(db_session, guest.id)
    if start_status == BookingStatus.IN_PROGRESS:
        await checkin(client, owner_headers, booking["id"])
    await end_stay_period(db_session, booking["id"])

    response = await close(client, await staff(db_session, client), booking["id"], "complete")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    [payout] = await payouts_of(db_session, booking["id"])
    assert (payout.recipient_id, payout.amount, payout.status) == (
        owner.id,
        Decimal("9000.00"),  # 10000 − 10%
        PayoutStatus.PAID,
    )
    assert ("payout_paid", {"booking_id": booking["id"]}) in await notifications_of(
        db_session, owner.id
    )


async def test_vehicle_no_show_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    """no_show — только для stay → 400."""
    _, _, vehicle_id = await make_vehicle(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    booking = await rent(client, headers, rent_body(vehicle_id, 1, 3))
    await pay(client, headers, booking["id"], "pay-vehicle")
    await end_stay_period(db_session, booking["id"], 1)
    mod = await staff(db_session, client)
    assert_error(await close(client, mod, booking["id"], "no-show"), 400, "domain_error")
    assert await payouts_of(db_session, booking["id"]) == []
