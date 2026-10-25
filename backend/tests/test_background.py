"""Фоновые задачи и уведомления: напоминания хозяину каждые 8 ч, закрытие броней,
удаление неоплаченных, web push, уведомления о событиях."""

import base64
from datetime import datetime, time, timedelta

import pywebpush
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import select

from app import rules, worker
from app.admin import emergency_cancel
from app.config import get_settings
from app.models import PushSubscription, SupportTicket, User, UserRole
from tests.conftest import (
    SessionFactory,
    auth,
    book_stay,
    confirmed_stay,
    make_host,
    make_user,
)


def _shift_now(monkeypatch, delta: timedelta) -> None:
    moment = datetime.now(rules.TZ) + delta
    monkeypatch.setattr(rules, "now_bishkek", lambda: moment)


async def _types(client, user) -> list[str]:
    items = (await client.get("/api/v1/notifications", headers=auth(user))).json()["items"]
    return [n["type"] for n in items]


async def test_host_reminders_every_8_hours_until_acknowledged(client, monkeypatch):
    host = await make_host()
    created = await confirmed_stay(client, await make_user(), host, 3, 4)
    await book_stay(client, await make_user(), await make_host(), 3, 4)  # не оплачена

    assert await worker.remind_hosts({}) == 1  # сразу после оплаты
    assert await worker.remind_hosts({}) == 0
    _shift_now(monkeypatch, timedelta(hours=7, minutes=59))
    assert await worker.remind_hosts({}) == 0
    _shift_now(monkeypatch, timedelta(hours=8, minutes=1))
    assert await worker.remind_hosts({}) == 1
    assert await _types(client, host) == ["host_reminder", "host_reminder"]

    await client.post(
        f"/api/v1/bookings/{created['booking']['id']}/acknowledge", headers=auth(host)
    )
    _shift_now(monkeypatch, timedelta(hours=24))
    assert await worker.remind_hosts({}) == 0


async def test_bookings_closed_at_checkout_time(client, monkeypatch):
    """12:00 дня выезда: заселённая бронь → completed, без заселения по PIN → failed."""
    host, guest, no_show = await make_host(capacity=2), await make_user(), await make_user()
    checked_in = await confirmed_stay(client, guest, host, 0, 1)
    not_checked_in = await confirmed_stay(client, no_show, host, 0, 1)
    await client.post(
        f"/api/v1/bookings/{checked_in['booking']['id']}/check-in",
        headers=auth(host),
        json={"pin": guest.checkin_pin},
    )
    checkout_day = rules.today_bishkek() + timedelta(days=1)

    before = datetime.combine(checkout_day, time(11, 59), tzinfo=rules.TZ)
    monkeypatch.setattr(rules, "now_bishkek", lambda: before)
    assert await worker.close_bookings({}) == 0

    at_checkout = datetime.combine(checkout_day, time(12, 0), tzinfo=rules.TZ)
    monkeypatch.setattr(rules, "now_bishkek", lambda: at_checkout)
    assert await worker.close_bookings({}) == 2
    for user, created, status in (
        (guest, checked_in, "completed"),
        (no_show, not_checked_in, "failed"),
    ):
        booking = await client.get(
            f"/api/v1/bookings/{created['booking']['id']}", headers=auth(user)
        )
        assert booking.json()["status"] == status


async def test_unpaid_booking_deleted_after_30_minutes(client, monkeypatch):
    host = await make_host(capacity=1)
    guest = await make_user()
    created = await book_stay(client, guest, host, 2, 3)
    payer = await make_user()
    paid = await confirmed_stay(client, payer, await make_host(), 2, 3)

    _shift_now(monkeypatch, timedelta(minutes=29))
    assert await worker.expire_payments({}) == 0
    _shift_now(monkeypatch, timedelta(minutes=31))
    assert await worker.expire_payments({}) == 1

    url = f"/api/v1/bookings/{created['booking']['id']}"
    assert (await client.get(url, headers=auth(guest))).status_code == 404
    paid_url = f"/api/v1/bookings/{paid['booking']['id']}"
    assert (
        await client.get(paid_url, headers=auth(payer))
    ).status_code == 200  # оплаченная осталась
    monkeypatch.undo()
    assert (await book_stay(client, await make_user(), host, 2, 3))["status"] == 201


def _browser_keys() -> dict[str, str]:
    """Ключи, которые браузер отдаёт в PushSubscription (p256dh и auth)."""
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    encode = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
    return {"p256dh": encode(public), "auth": encode(b"0123456789abcdef")}


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.text = ""
        self.reason = ""
        self.headers: dict[str, str] = {}


async def test_web_push_sent_once_and_dead_subscription_removed(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(
        settings, "vapid_private_key", "iMimXTKWTK43TGnWRYIsEDv70aGBIu57nfXdV4q1joQ"
    )
    sent: list[dict] = []
    status = {"code": 201}

    def fake_post(url, data=None, headers=None, timeout=None, **kwargs):
        sent.append({"url": url, "headers": headers, "size": len(data)})
        return _FakeResponse(status["code"])

    monkeypatch.setattr(pywebpush.requests, "post", fake_post)

    host = await make_host()
    endpoint = "https://push.example.com/send/abc"
    response = await client.post(
        "/api/v1/notifications/push/subscriptions",
        headers=auth(host),
        json={"endpoint": endpoint, "keys": _browser_keys()},
    )
    assert response.status_code == 204

    await confirmed_stay(client, await make_user(), host, 3, 4)
    await worker.remind_hosts({})
    assert await worker.push_notifications({}) == 1
    assert await worker.push_notifications({}) == 0  # повторно не отправляется
    assert len(sent) == 1
    assert sent[0]["url"] == endpoint
    assert sent[0]["headers"]["Authorization"].startswith("vapid ")
    assert sent[0]["size"] > 0  # зашифрованное тело

    # Браузер отписался (410) — подписка удаляется.
    status["code"] = 410
    await confirmed_stay(client, await make_user(), host, 6, 7)
    await worker.remind_hosts({})
    await worker.push_notifications({})
    async with SessionFactory() as session:
        assert not list(await session.scalars(select(PushSubscription)))


async def test_notifications_on_chat_and_cancellations(client):
    host, guest = await make_host(), await make_user()
    conversation = await client.post(
        "/api/v1/conversations", headers=auth(guest), json={"owner_id": host.id}
    )
    await client.post(
        f"/api/v1/conversations/{conversation.json()['id']}/messages",
        headers=auth(guest),
        json={"text": "Салам!"},
    )
    assert await _types(client, host) == ["new_message"]

    by_guest = await confirmed_stay(client, guest, host, 10, 11)
    await client.post(
        f"/api/v1/bookings/{by_guest['booking']['id']}/cancel", headers=auth(guest), json={}
    )
    assert (await _types(client, host))[0] == "booking_cancelled"

    by_admin = await confirmed_stay(client, guest, host, 20, 21)
    async with SessionFactory() as session:
        await emergency_cancel(session, by_admin["booking"]["id"], host.id, reason_valid=True)
    assert (await _types(client, guest)) == ["booking_cancelled"]
    assert (await _types(client, host))[0] == "booking_cancelled"


async def test_notification_on_support_reply_from_admin_panel(client):
    author = await make_user()
    ticket = await client.post(
        "/api/v1/support/tickets", headers=auth(author), json={"description": "Вопрос"}
    )
    admin = await make_user(name="admin")
    async with SessionFactory() as session:
        (await session.get(User, admin.id)).role = UserRole.ADMIN
        await session.commit()

    login = await client.post(
        "/admin/login", data={"username": admin.email, "password": "password123"}
    )
    assert login.status_code == 302
    reply = await client.post(
        "/admin/support-message/create",
        data={"ticket_id": ticket.json()["id"], "text": "Ответ поддержки", "save": "Save"},
    )
    assert reply.status_code == 302
    assert await _types(client, author) == ["support_reply"]
    detail = await client.get(
        f"/api/v1/support/tickets/{ticket.json()['id']}", headers=auth(author)
    )
    assert [m["text"] for m in detail.json()["messages"]] == ["Ответ поддержки"]
    async with SessionFactory() as session:
        assert (await session.get(SupportTicket, ticket.json()["id"])) is not None


async def test_notifications_are_private_and_can_be_read(client):
    host = await make_host()
    await confirmed_stay(client, await make_user(), host, 3, 4)
    await worker.remind_hosts({})
    notification = (await client.get("/api/v1/notifications", headers=auth(host))).json()["items"][
        0
    ]

    stranger = await make_user()
    assert await _types(client, stranger) == []
    url = f"/api/v1/notifications/{notification['id']}/read"
    assert (await client.post(url, headers=auth(stranger))).status_code == 404

    read = await client.post(url, headers=auth(host))
    assert read.json()["read_at"] is not None
    unread = await client.get("/api/v1/notifications?unread_only=true", headers=auth(host))
    assert unread.json()["total"] == 0
