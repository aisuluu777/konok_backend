"""Отмена гостем: POST /bookings/{id}/cancel.

R-22 — хозяин отменить не может (403); R-34 — полный возврат каждого успешного платежа;
R-28 — отмена в audit_logs; R-39 — уведомление о возврате гостю.
"""

import asyncio
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.modules.bookings.models import BookingStatus
from app.modules.payments import provider as provider_module
from app.modules.payments.models import Payment, PaymentStatus, Refund, RefundStatus
from app.modules.payments.provider import MockPaymentProvider
from tests.activities.test_activities import activity_body, create_activity
from tests.admin.helpers import assert_error
from tests.bookings.test_checkin import checkin, confirmed_stay, payouts_of
from tests.bookings.test_stay_bookings import (
    book,
    booking_status,
    guest_with_headers,
    set_status,
    stay_body,
)
from tests.bookings.test_vehicle_bookings import make_vehicle, rent, rent_body
from tests.payments.test_add_activities import add
from tests.payments.test_pay import notifications_of, pay, payments_of, stay_hold


async def cancel(
    client: AsyncClient, headers: dict[str, str], booking_id: int, **body: Any
) -> Response:
    return await client.post(f"/bookings/{booking_id}/cancel", headers=headers, json=body)


async def refunds_of(db_session: AsyncSession, booking_id: int) -> list[Refund]:
    rows = await db_session.scalars(
        select(Refund)
        .join(Payment, Payment.id == Refund.payment_id)
        .where(Payment.booking_id == booking_id)
        .order_by(Refund.id)
    )
    return list(rows.all())


async def test_requires_auth(client: AsyncClient) -> None:
    assert_error(await client.post("/bookings/1/cancel", json={}), 401, "unauthorized")


async def test_cancel_confirmed_refunds_all_payments(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-34: возвращаются оба платежа (бронь и докупка) полной суммой, платежи → refunded."""
    host_id, host_headers, headers, booking = await confirmed_stay(
        db_session, client, price="1000.00", start=1, end=2
    )
    act = await create_activity(client, host_headers, activity_body(price_per_person="500.00"))
    await add(client, headers, booking["id"], [{"activity_id": act["id"], "persons": 1}])

    response = await cancel(client, headers, booking["id"], reason="Планы изменились")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled_by_guest"
    payments = await payments_of(db_session, booking["id"])
    refunds = await refunds_of(db_session, booking["id"])
    assert [p.status for p in payments] == [PaymentStatus.REFUNDED] * 2
    assert [(r.payment_id, r.amount) for r in refunds] == [(p.id, p.amount) for p in payments]
    assert all(r.status == RefundStatus.SUCCEEDED and r.processed_at for r in refunds)
    assert all(r.initiated_by == booking["guest_id"] for r in refunds)
    assert all(r.reason == "Планы изменились" for r in refunds)
    assert await booking_status(db_session, booking["id"]) == "cancelled_by_guest"
    row = (
        await db_session.execute(
            text("SELECT cancelled_by, cancelled_at, cancel_reason FROM bookings WHERE id = :id"),
            {"id": booking["id"]},
        )
    ).one()
    assert row.cancelled_by == booking["guest_id"] and row.cancelled_at is not None
    assert row.cancel_reason == "Планы изменились"


async def test_cancel_frees_the_dates(client: AsyncClient, db_session: AsyncSession) -> None:
    """После отмены ночи свободны — другой гость может забронировать то же."""
    host_id, _, headers, booking = await confirmed_stay(
        db_session, client, start=1, end=3, guests_count=4
    )
    await cancel(client, headers, booking["id"])
    _, other = await guest_with_headers(db_session, client)
    await book(client, other, stay_body(host_id, start=1, end=3, guests_count=4))


async def test_cancel_hold_without_payments(client: AsyncClient, db_session: AsyncSession) -> None:
    """hold отменяется без возвратов и без уведомления о возврате."""
    _, _, headers, booking = await stay_hold(db_session, client)

    response = await cancel(client, headers, booking["id"])

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled_by_guest"
    assert await refunds_of(db_session, booking["id"]) == []
    assert await notifications_of(db_session, booking["guest_id"]) == []


async def test_failed_payment_not_refunded(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Возвращаются только succeeded: failed-платёж остаётся failed."""
    _, _, headers, booking = await stay_hold(db_session, client)
    monkeypatch.setattr(provider_module, "_provider", MockPaymentProvider(fail=True))
    await pay(client, headers, booking["id"])

    await cancel(client, headers, booking["id"])

    [payment] = await payments_of(db_session, booking["id"])
    assert payment.status == PaymentStatus.FAILED
    assert await refunds_of(db_session, booking["id"]) == []


async def test_cancel_vehicle_booking(client: AsyncClient, db_session: AsyncSession) -> None:
    """Аренда машины отменяется так же, возврат полный."""
    _, owner_headers, vehicle_id = await make_vehicle(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    booking = await rent(client, headers, rent_body(vehicle_id))
    await pay(client, headers, booking["id"], "pay-vehicle")

    assert_error(await cancel(client, owner_headers, booking["id"]), 403, "permission_denied")
    response = await cancel(client, headers, booking["id"])

    assert response.status_code == 200
    [refund] = await refunds_of(db_session, booking["id"])
    assert refund.amount == Decimal("10000.00")


async def test_host_cannot_cancel(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-22: хозяин → 403, бронь и платёж не тронуты; посторонний → 404."""
    _, host_headers, _, booking = await confirmed_stay(db_session, client)
    _, stranger = await guest_with_headers(db_session, client)

    assert_error(await cancel(client, host_headers, booking["id"]), 403, "permission_denied")
    assert_error(await cancel(client, stranger, booking["id"]), 404, "not_found")

    assert await booking_status(db_session, booking["id"]) == "confirmed"
    assert await refunds_of(db_session, booking["id"]) == []


@pytest.mark.parametrize(
    "status",
    [
        BookingStatus.IN_PROGRESS,
        BookingStatus.COMPLETED,
        BookingStatus.CANCELLED_BY_GUEST,
        BookingStatus.EXPIRED,
        BookingStatus.NO_SHOW,
    ],
)
async def test_cancel_only_hold_or_confirmed(
    client: AsyncClient, db_session: AsyncSession, status: BookingStatus
) -> None:
    """Из остальных статусов отмена невозможна → 409, возвратов нет."""
    _, _, headers, booking = await confirmed_stay(db_session, client)
    await set_status(db_session, booking["id"], status)
    assert_error(await cancel(client, headers, booking["id"]), 409, "conflict")
    assert await refunds_of(db_session, booking["id"]) == []


async def test_cannot_cancel_after_checkin(client: AsyncClient, db_session: AsyncSession) -> None:
    """После заселения (выплата проведена) отмена → 409."""
    _, host_headers, headers, booking = await confirmed_stay(db_session, client)
    await checkin(client, host_headers, booking["id"])
    assert_error(await cancel(client, headers, booking["id"]), 409, "conflict")


async def test_repeat_cancel_conflict(client: AsyncClient, db_session: AsyncSession) -> None:
    """Повторная отмена → 409 и второго возврата нет."""
    _, _, headers, booking = await confirmed_stay(db_session, client)
    await cancel(client, headers, booking["id"])
    assert_error(await cancel(client, headers, booking["id"]), 409, "conflict")
    assert len(await refunds_of(db_session, booking["id"])) == 1


async def test_refund_notification(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-39: гостю — refund с суммой возврата."""
    _, _, headers, booking = await confirmed_stay(
        db_session, client, price="1000.00", start=1, end=2
    )
    await cancel(client, headers, booking["id"])
    assert (
        "refund",
        {"booking_id": booking["id"], "amount": "1000.00"},
    ) in await notifications_of(db_session, booking["guest_id"])


async def test_cancel_audit(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-28: отмена пишется в audit_logs с причиной и суммой возврата."""
    _, _, headers, booking = await confirmed_stay(
        db_session, client, price="1000.00", start=1, end=2
    )
    await cancel(client, headers, booking["id"], reason="Болезнь")
    log = await db_session.scalar(select(AuditLog).where(AuditLog.action == "booking_cancelled"))
    assert log is not None
    assert (log.actor_id, log.entity_type, log.entity_id) == (
        booking["guest_id"],
        "booking",
        booking["id"],
    )
    assert log.details == {
        "previous_status": "confirmed",
        "reason": "Болезнь",
        "refunded": "1000.00",
    }


@pytest.mark.parametrize("body", [{"reason": ""}, {"reason": "x" * 501}, {"reason": 5}])
async def test_reason_invalid(
    client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    _, _, headers, booking = await confirmed_stay(db_session, client)
    assert_error(await cancel(client, headers, booking["id"], **body), 422, "validation_error")


async def test_parallel_cancel_and_checkin(client: AsyncClient, db_session: AsyncSession) -> None:
    """Гонка отмены и заселения: выигрывает одно, второе 409; не бывает и возврата, и выплаты."""
    _, host_headers, headers, booking = await confirmed_stay(db_session, client)
    cancelled, checked_in = await asyncio.gather(
        cancel(client, headers, booking["id"]), checkin(client, host_headers, booking["id"])
    )
    assert sorted([cancelled.status_code, checked_in.status_code]) == [200, 409]
    refunds = await refunds_of(db_session, booking["id"])
    payouts = await payouts_of(db_session, booking["id"])
    assert len(refunds) + len(payouts) == 1
