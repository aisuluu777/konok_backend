"""In-app уведомления пользователя: список и явная отметка о прочтении (R-39)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.models import Notification
from app.modules.notifications.service import create_notification
from app.modules.users.models import User
from tests.admin.helpers import assert_error
from tests.factories import auth, create_user, login

NOTIFICATION_FIELDS = {"id", "type", "channel", "payload", "read_at", "created_at"}
BIGINT_MAX = 2**63 - 1


async def user_with_headers(
    db_session: AsyncSession, client: AsyncClient
) -> tuple[User, dict[str, str]]:
    user = await create_user(db_session)
    tokens = await login(client, user.email)
    return user, auth(tokens["access_token"])


async def notify(
    db_session: AsyncSession,
    user: User,
    type_: str = "new_booking",
    payload: dict[str, Any] | None = None,
    *,
    created_at: datetime | None = None,
    read_at: datetime | None = None,
) -> Notification:
    """Создаёт in_app уведомление через сервисный хелпер и коммитит."""
    notification = create_notification(db_session, user_id=user.id, type=type_, payload=payload)
    if created_at is not None:
        notification.created_at = created_at
    if read_at is not None:
        notification.read_at = read_at
    await db_session.commit()
    await db_session.refresh(notification)
    return notification


async def read_at_in_db(db_session: AsyncSession, notification_id: int) -> datetime | None:
    # Выборка колонки, не сущности: значение берётся из БД, а не из identity map.
    value: datetime | None = await db_session.scalar(
        select(Notification.read_at).where(Notification.id == notification_id)
    )
    return value


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def list_notifications(client: AsyncClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    response = await client.get("/notifications", headers=headers)
    assert response.status_code == 200, response.text
    data: list[dict[str, Any]] = response.json()
    return data


# --- без токена ---


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/notifications"),
        ("POST", "/notifications/1/read"),
        ("POST", "/notifications/read-all"),
    ],
)
async def test_notifications_require_auth(client: AsyncClient, method: str, path: str) -> None:
    """R-39: уведомления читает только аутентифицированный user — без токена 401 unauthorized."""
    response = await client.request(method, path)

    assert_error(response, 401, "unauthorized")


# --- GET /notifications ---


async def test_list_returns_own_notification_shape(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-39: GET /notifications → свои in_app уведомления в формате NotificationOut."""
    user, headers = await user_with_headers(db_session, client)
    n = await notify(db_session, user, "booking_confirmed", {"booking_id": 7})

    data = await list_notifications(client, headers)

    assert len(data) == 1
    item = data[0]
    assert set(item) == NOTIFICATION_FIELDS
    assert item["id"] == n.id
    assert item["type"] == "booking_confirmed"
    assert item["channel"] == "in_app"
    assert item["payload"] == {"booking_id": 7}
    assert item["read_at"] is None
    assert parse_ts(item["created_at"]) == n.created_at


async def test_list_empty_for_user_without_notifications(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-39 (граница): у пользователя нет уведомлений → 200 []."""
    _, headers = await user_with_headers(db_session, client)

    assert await list_notifications(client, headers) == []


async def test_list_excludes_other_users_notifications(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-39 (нарушение): user видит только свои уведомления, чужие в списке отсутствуют."""
    user, headers = await user_with_headers(db_session, client)
    other = await create_user(db_session)
    mine = await notify(db_session, user)
    await notify(db_session, other)

    data = await list_notifications(client, headers)

    assert [item["id"] for item in data] == [mine.id]


async def test_list_excludes_non_in_app_channels(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-39: в MVP канал только in_app — уведомления с channel='email'/'sms' в списке не видны."""
    user, headers = await user_with_headers(db_session, client)
    in_app = await notify(db_session, user)
    db_session.add_all(
        [
            Notification(user_id=user.id, type="new_booking", channel="email", payload={}),
            Notification(user_id=user.id, type="new_booking", channel="sms", payload={}),
        ]
    )
    await db_session.commit()

    data = await list_notifications(client, headers)

    assert [item["id"] for item in data] == [in_app.id]


async def test_list_ordered_newest_first(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-39: список отсортирован по created_at DESC — новые первыми."""
    user, headers = await user_with_headers(db_session, client)
    base = datetime.now(UTC)
    oldest = await notify(db_session, user, created_at=base - timedelta(hours=2))
    newest = await notify(db_session, user, created_at=base)
    middle = await notify(db_session, user, created_at=base - timedelta(hours=1))

    data = await list_notifications(client, headers)

    assert [item["id"] for item in data] == [newest.id, middle.id, oldest.id]


async def test_list_same_created_at_ordered_by_id_desc(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-39 (граница): при равном created_at порядок по id DESC."""
    user, headers = await user_with_headers(db_session, client)
    same = datetime.now(UTC)
    first = await notify(db_session, user, created_at=same)
    second = await notify(db_session, user, created_at=same)
    third = await notify(db_session, user, created_at=same)

    data = await list_notifications(client, headers)

    assert [item["id"] for item in data] == [third.id, second.id, first.id]


async def test_list_does_not_mark_as_read(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-39: GET не меняет read_at — после чтения списка уведомление в БД непрочитано."""
    user, headers = await user_with_headers(db_session, client)
    n = await notify(db_session, user)

    await list_notifications(client, headers)
    data = await list_notifications(client, headers)

    assert data[0]["read_at"] is None
    assert await read_at_in_db(db_session, n.id) is None


async def test_list_shows_read_at_of_read_notification(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-39: прочитанное уведомление остаётся в списке, read_at — ISO-время прочтения."""
    user, headers = await user_with_headers(db_session, client)
    read_time = datetime.now(UTC) - timedelta(minutes=5)
    n = await notify(db_session, user, read_at=read_time)

    data = await list_notifications(client, headers)

    assert [item["id"] for item in data] == [n.id]
    assert parse_ts(data[0]["read_at"]) == read_time


# --- POST /notifications/{id}/read ---


async def test_mark_read_sets_read_at(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-39: POST /notifications/{id}/read → 200 NotificationOut с read_at, в БД он выставлен."""
    user, headers = await user_with_headers(db_session, client)
    n = await notify(db_session, user, payload={"booking_id": 1})

    response = await client.post(f"/notifications/{n.id}/read", headers=headers)

    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data) == NOTIFICATION_FIELDS
    assert data["id"] == n.id
    assert data["channel"] == "in_app"
    assert data["payload"] == {"booking_id": 1}
    assert data["read_at"] is not None
    stored = await read_at_in_db(db_session, n.id)
    assert stored is not None
    assert parse_ts(data["read_at"]) == stored


async def test_mark_read_twice_keeps_first_read_at(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-39 (граница): повторная отметка → 200, read_at не меняется (сохраняется первое время)."""
    user, headers = await user_with_headers(db_session, client)
    n = await notify(db_session, user)

    first = await client.post(f"/notifications/{n.id}/read", headers=headers)
    assert first.status_code == 200, first.text
    first_db = await read_at_in_db(db_session, n.id)

    second = await client.post(f"/notifications/{n.id}/read", headers=headers)

    assert second.status_code == 200, second.text
    assert parse_ts(second.json()["read_at"]) == parse_ts(first.json()["read_at"])
    assert await read_at_in_db(db_session, n.id) == first_db


async def test_mark_read_already_read_keeps_original_read_at(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-39 (граница): уведомление прочитано ранее → отметка не перезаписывает read_at."""
    user, headers = await user_with_headers(db_session, client)
    read_time = datetime.now(UTC) - timedelta(days=1)
    n = await notify(db_session, user, read_at=read_time)

    response = await client.post(f"/notifications/{n.id}/read", headers=headers)

    assert response.status_code == 200, response.text
    assert parse_ts(response.json()["read_at"]) == read_time
    assert await read_at_in_db(db_session, n.id) == read_time


async def test_mark_read_other_users_notification_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-39 (нарушение): чужое уведомление → 404 not_found, в БД оно остаётся непрочитанным."""
    _, headers = await user_with_headers(db_session, client)
    other = await create_user(db_session)
    foreign = await notify(db_session, other)

    response = await client.post(f"/notifications/{foreign.id}/read", headers=headers)

    assert_error(response, 404, "not_found")
    assert await read_at_in_db(db_session, foreign.id) is None


async def test_mark_read_nonexistent_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-39 (граница): несуществующий id → 404 not_found."""
    _, headers = await user_with_headers(db_session, client)

    response = await client.post("/notifications/999999/read", headers=headers)

    assert_error(response, 404, "not_found")


@pytest.mark.parametrize("bad_id", ["0", str(BIGINT_MAX + 1)])
async def test_mark_read_invalid_id_rejected(
    client: AsyncClient, db_session: AsyncSession, bad_id: str
) -> None:
    """R-39 (граница): id = 0 или вне диапазона BIGINT → 422."""
    _, headers = await user_with_headers(db_session, client)

    response = await client.post(f"/notifications/{bad_id}/read", headers=headers)

    assert response.status_code == 422, response.text
    assert "error" in response.json(), response.text


async def test_mark_read_bigint_max_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-39 (граница): id = максимум BIGINT — валиден, но не существует → 404 not_found."""
    _, headers = await user_with_headers(db_session, client)

    response = await client.post(f"/notifications/{BIGINT_MAX}/read", headers=headers)

    assert_error(response, 404, "not_found")


# --- POST /notifications/read-all ---


async def test_read_all_marks_own_unread(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-39: read-all → 200 {"updated": n}, все свои непрочитанные in_app теперь прочитаны."""
    user, headers = await user_with_headers(db_session, client)
    first = await notify(db_session, user)
    second = await notify(db_session, user)

    response = await client.post("/notifications/read-all", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"updated": 2}
    assert await read_at_in_db(db_session, first.id) is not None
    assert await read_at_in_db(db_session, second.id) is not None


async def test_read_all_skips_already_read(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-39 (граница): уже прочитанные не считаются в updated и сохраняют прежний read_at."""
    user, headers = await user_with_headers(db_session, client)
    read_time = datetime.now(UTC) - timedelta(days=1)
    already = await notify(db_session, user, read_at=read_time)
    unread = await notify(db_session, user)

    response = await client.post("/notifications/read-all", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"updated": 1}
    assert await read_at_in_db(db_session, already.id) == read_time
    assert await read_at_in_db(db_session, unread.id) is not None


async def test_read_all_does_not_touch_other_users(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-39 (нарушение): read-all не трогает чужие уведомления."""
    user, headers = await user_with_headers(db_session, client)
    other = await create_user(db_session)
    await notify(db_session, user)
    foreign = await notify(db_session, other)

    response = await client.post("/notifications/read-all", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"updated": 1}
    assert await read_at_in_db(db_session, foreign.id) is None


async def test_read_all_ignores_non_in_app(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-39: read-all считает только in_app — уведомление с channel='email' не учитывается."""
    user, headers = await user_with_headers(db_session, client)
    await notify(db_session, user)
    email = Notification(user_id=user.id, type="new_booking", channel="email", payload={})
    db_session.add(email)
    await db_session.commit()

    response = await client.post("/notifications/read-all", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"updated": 1}
    assert await read_at_in_db(db_session, email.id) is None


async def test_read_all_repeat_updates_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-39 (граница): повторный read-all → {"updated": 0}, read_at не меняется."""
    user, headers = await user_with_headers(db_session, client)
    n = await notify(db_session, user)
    first = await client.post("/notifications/read-all", headers=headers)
    assert first.json() == {"updated": 1}, first.text
    first_db = await read_at_in_db(db_session, n.id)

    response = await client.post("/notifications/read-all", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"updated": 0}
    assert await read_at_in_db(db_session, n.id) == first_db


async def test_read_all_without_notifications(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-39 (граница): нет уведомлений → 200 {"updated": 0}."""
    _, headers = await user_with_headers(db_session, client)

    response = await client.post("/notifications/read-all", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"updated": 0}


# --- хелпер create_notification ---


async def test_create_notification_does_not_commit(db_session: AsyncSession) -> None:
    """R-39: уведомление пишется в транзакции события — хелпер не коммитит, rollback отменяет."""
    user = await create_user(db_session)
    user_id = user.id
    create_notification(db_session, user_id=user_id, type="new_booking")
    await db_session.flush()

    await db_session.rollback()

    rows = (
        await db_session.scalars(select(Notification).where(Notification.user_id == user_id))
    ).all()
    assert rows == []


async def test_create_notification_defaults(db_session: AsyncSession) -> None:
    """R-39: хелпер создаёт in_app уведомление, payload по умолчанию {}, read_at пуст."""
    user = await create_user(db_session)
    n = create_notification(db_session, user_id=user.id, type="kyc_approved")
    await db_session.commit()

    stored = await db_session.get(Notification, n.id)
    assert stored is not None
    await db_session.refresh(stored)
    assert stored.user_id == user.id
    assert stored.type == "kyc_approved"
    assert stored.channel == "in_app"
    assert stored.payload == {}
    assert stored.read_at is None
    assert stored.created_at is not None


async def test_mark_read_non_in_app_notification_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-39: отмечать можно только in-app уведомления; своё email-уведомление — 404."""
    user, headers = await user_with_headers(db_session, client)
    email_notification = Notification(user_id=user.id, type="payment", channel="email")
    db_session.add(email_notification)
    await db_session.commit()

    response = await client.post(f"/notifications/{email_notification.id}/read", headers=headers)

    assert_error(response, 404, "not_found")
    assert await read_at_in_db(db_session, email_notification.id) is None
