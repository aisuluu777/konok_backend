"""Настройки платформы: чтение и изменение администратором с аудитом (R-28)."""

from datetime import datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.modules.admin.models import Setting
from app.modules.users.models import UserRole
from tests.admin.helpers import assert_error, headers_for

KEY = "hold_minutes"
SEED_VALUE = "15"


async def setting_value(db_session: AsyncSession, key: str) -> str | None:
    return await db_session.scalar(
        select(Setting.value).where(Setting.key == key).execution_options(populate_existing=True)
    )


async def audit_rows(db_session: AsyncSession) -> list[AuditLog]:
    result = await db_session.scalars(
        select(AuditLog).where(AuditLog.action == "setting_updated").order_by(AuditLog.id)
    )
    return list(result)


async def audit_count(db_session: AsyncSession) -> int:
    count = await db_session.scalar(select(func.count()).select_from(AuditLog))
    return int(count or 0)


async def admin_settings(client: AsyncClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    response = await client.get("/admin/settings", headers=headers)
    assert response.status_code == 200, response.text
    data: list[dict[str, Any]] = response.json()
    return data


# --- чтение ---


async def test_admin_lists_settings_sorted(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-28: admin видит все 10 настроек из сидов, отсортированные по key."""
    _, headers = await headers_for(client, db_session)

    data = await admin_settings(client, headers)

    assert len(data) == 10
    assert all(set(s) == {"key", "value", "updated_by", "updated_at"} for s in data), data
    keys = [s["key"] for s in data]
    assert keys == sorted(keys)
    hold = next(s for s in data if s["key"] == KEY)
    assert hold["value"] == SEED_VALUE
    assert hold["updated_by"] is None


# --- изменение ---


async def test_admin_updates_setting_and_audit_written(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-28: изменение настройки → 200, updated_by = админ, запись setting_updated в audit_logs
    со старым и новым значением."""
    admin, headers = await headers_for(client, db_session)
    before = next(s for s in await admin_settings(client, headers) if s["key"] == KEY)

    response = await client.put(f"/admin/settings/{KEY}", json={"value": "20"}, headers=headers)

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["key"] == KEY
    assert data["value"] == "20"
    assert data["updated_by"] == admin.id
    assert datetime.fromisoformat(data["updated_at"]) >= datetime.fromisoformat(
        before["updated_at"]
    )
    assert await setting_value(db_session, KEY) == "20"
    listed = next(s for s in await admin_settings(client, headers) if s["key"] == KEY)
    assert listed == data

    rows = await audit_rows(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_id == admin.id
    assert row.entity_type == "setting"
    assert row.entity_id is None
    assert row.details == {"key": KEY, "old_value": SEED_VALUE, "new_value": "20"}


async def test_update_does_not_touch_other_settings(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-28 (граница): меняется только указанный ключ, остальные настройки прежние."""
    _, headers = await headers_for(client, db_session)
    before = {s["key"]: s for s in await admin_settings(client, headers)}

    response = await client.put(f"/admin/settings/{KEY}", json={"value": "30"}, headers=headers)

    assert response.status_code == 200, response.text
    after = {s["key"]: s for s in await admin_settings(client, headers)}
    assert set(after) == set(before)
    for key in before:
        if key != KEY:
            assert after[key] == before[key], key


async def test_consecutive_updates_audit_chain(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-28: каждое изменение — отдельная запись; old_value второй = new_value первой."""
    _, headers = await headers_for(client, db_session)

    first = await client.put(f"/admin/settings/{KEY}", json={"value": "20"}, headers=headers)
    second = await client.put(f"/admin/settings/{KEY}", json={"value": "25"}, headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    details = [row.details for row in await audit_rows(db_session)]
    assert details == [
        {"key": KEY, "old_value": SEED_VALUE, "new_value": "20"},
        {"key": KEY, "old_value": "20", "new_value": "25"},
    ]


async def test_update_unknown_key_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-28: несуществующий ключ → 404 not_found, новая настройка не создаётся, аудита нет."""
    _, headers = await headers_for(client, db_session)

    response = await client.put("/admin/settings/no_such_key", json={"value": "1"}, headers=headers)

    assert_error(response, 404, "not_found")
    assert await setting_value(db_session, "no_such_key") is None
    assert len(await admin_settings(client, headers)) == 10
    assert await audit_count(db_session) == 0


@pytest.mark.parametrize("body", [{"value": ""}, {}, {"value": None}])
async def test_update_setting_invalid_value_rejected(
    client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    """R-28 (граница): пустое/отсутствующее значение → 422 validation_error,
    значение не меняется, аудита нет."""
    _, headers = await headers_for(client, db_session)

    response = await client.put(f"/admin/settings/{KEY}", json=body, headers=headers)

    assert_error(response, 422, "validation_error")
    assert await setting_value(db_session, KEY) == SEED_VALUE
    assert await audit_count(db_session) == 0


# --- доступ ---


@pytest.mark.parametrize("role", [UserRole.USER, UserRole.MODERATOR])
async def test_non_admin_cannot_update_setting(
    client: AsyncClient, db_session: AsyncSession, role: UserRole
) -> None:
    """R-28: менять настройки может только admin — user/moderator → 403,
    значение прежнее, аудита нет."""
    _, headers = await headers_for(client, db_session, role)

    response = await client.put(f"/admin/settings/{KEY}", json={"value": "99"}, headers=headers)

    assert_error(response, 403, "permission_denied")
    assert await setting_value(db_session, KEY) == SEED_VALUE
    assert await audit_count(db_session) == 0


@pytest.mark.parametrize("role", [UserRole.USER, UserRole.MODERATOR])
async def test_non_admin_cannot_list_settings(
    client: AsyncClient, db_session: AsyncSession, role: UserRole
) -> None:
    """R-28: список настроек — admin-only, user/moderator → 403."""
    _, headers = await headers_for(client, db_session, role)

    response = await client.get("/admin/settings", headers=headers)

    assert_error(response, 403, "permission_denied")


async def test_settings_require_auth(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-28: без токена GET и PUT → 401 unauthorized, значение не меняется, аудита нет."""
    listed = await client.get("/admin/settings")
    updated = await client.put(f"/admin/settings/{KEY}", json={"value": "99"})

    assert_error(listed, 401, "unauthorized")
    assert_error(updated, 401, "unauthorized")
    assert await setting_value(db_session, KEY) == SEED_VALUE
    assert await audit_count(db_session) == 0
