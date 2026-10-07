"""Блокировка пользователя администратором: денилист, отзыв refresh, аудит (R-01, R-28)."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.modules.users.models import User, UserRole, UserStatus
from tests.factories import DEFAULT_PASSWORD, auth, create_user, login


def assert_error(response_json: dict[str, Any], code: str) -> None:
    assert "error" in response_json, response_json
    assert response_json["error"]["code"] == code


async def admin_headers(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[User, dict[str, str]]:
    admin = await create_user(db_session, role=UserRole.ADMIN)
    tokens = await login(client, admin.email)
    return admin, auth(tokens["access_token"])


async def audit_rows(db_session: AsyncSession, action: str, entity_id: int) -> list[AuditLog]:
    result = await db_session.scalars(
        select(AuditLog).where(
            AuditLog.action == action,
            AuditLog.entity_type == "user",
            AuditLog.entity_id == entity_id,
        )
    )
    return list(result)


async def test_admin_blocks_user_and_audit_written(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-28, R-01: admin блокирует user → status blocked, запись user_blocked в audit_logs."""
    admin, headers = await admin_headers(client, db_session)
    target = await create_user(db_session)

    response = await client.post(f"/admin/users/{target.id}/block", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["id"] == target.id
    assert response.json()["status"] == "blocked"
    rows = await audit_rows(db_session, "user_blocked", target.id)
    assert len(rows) == 1
    assert rows[0].actor_id == admin.id


async def test_block_revokes_live_access_and_refresh(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-28 / Step 1 plan: живой access заблокированного отклоняется денилистом,
    refresh отозван, login → 403 user_blocked."""
    _, headers = await admin_headers(client, db_session)
    target = await create_user(db_session)
    tokens = await login(client, target.email)
    before = await client.get("/users/me", headers=auth(tokens["access_token"]))
    assert before.status_code == 200, before.text

    block = await client.post(f"/admin/users/{target.id}/block", headers=headers)
    assert block.status_code == 200, block.text

    me = await client.get("/users/me", headers=auth(tokens["access_token"]))
    assert me.status_code == 401, me.text
    assert_error(me.json(), "unauthorized")

    refresh = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh.status_code == 401, refresh.text
    assert_error(refresh.json(), "invalid_token")

    relogin = await client.post(
        "/auth/login", json={"email": target.email, "password": DEFAULT_PASSWORD}
    )
    assert relogin.status_code == 403, relogin.text
    assert_error(relogin.json(), "user_blocked")


async def test_block_does_not_affect_other_users(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-28 (граница): денилист бьёт только по заблокированному, чужие токены работают."""
    _, headers = await admin_headers(client, db_session)
    target = await create_user(db_session)
    bystander = await create_user(db_session)
    bystander_tokens = await login(client, bystander.email)

    block = await client.post(f"/admin/users/{target.id}/block", headers=headers)
    assert block.status_code == 200, block.text

    me = await client.get("/users/me", headers=auth(bystander_tokens["access_token"]))
    assert me.status_code == 200, me.text
    refresh = await client.post(
        "/auth/refresh", json={"refresh_token": bystander_tokens["refresh_token"]}
    )
    assert refresh.status_code == 200, refresh.text


async def test_unblock_restores_login_and_audit_written(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-28: unblock → status active, запись user_unblocked; login снова работает,
    новый access проходит /users/me."""
    admin, headers = await admin_headers(client, db_session)
    target = await create_user(db_session)
    block = await client.post(f"/admin/users/{target.id}/block", headers=headers)
    assert block.status_code == 200, block.text

    response = await client.post(f"/admin/users/{target.id}/unblock", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"
    rows = await audit_rows(db_session, "user_unblocked", target.id)
    assert len(rows) == 1
    assert rows[0].actor_id == admin.id

    tokens = await login(client, target.email)
    me = await client.get("/users/me", headers=auth(tokens["access_token"]))
    assert me.status_code == 200, me.text
    assert me.json()["status"] == "active"


async def test_block_twice_is_idempotent(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-28 (граница): повторный block заблокированного → 200, второй записи аудита нет."""
    _, headers = await admin_headers(client, db_session)
    target = await create_user(db_session)

    first = await client.post(f"/admin/users/{target.id}/block", headers=headers)
    second = await client.post(f"/admin/users/{target.id}/block", headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "blocked"
    assert len(await audit_rows(db_session, "user_blocked", target.id)) == 1


@pytest.mark.parametrize("role", [UserRole.USER, UserRole.MODERATOR])
@pytest.mark.parametrize("action", ["block", "unblock"])
async def test_non_admin_cannot_block(
    client: AsyncClient, db_session: AsyncSession, role: UserRole, action: str
) -> None:
    """R-01 / Step 1 plan (require_role): user и moderator не могут блокировать → 403,
    статус цели не меняется, аудит не пишется."""
    actor = await create_user(db_session, role=role)
    tokens = await login(client, actor.email)
    initial = UserStatus.ACTIVE if action == "block" else UserStatus.BLOCKED
    target = await create_user(db_session, status=initial)

    response = await client.post(
        f"/admin/users/{target.id}/{action}", headers=auth(tokens["access_token"])
    )

    assert response.status_code == 403, response.text
    assert_error(response.json(), "permission_denied")
    status = await db_session.scalar(
        select(User.status).where(User.id == target.id).execution_options(populate_existing=True)
    )
    assert status == initial
    assert list(await db_session.scalars(select(AuditLog))) == []


async def test_block_requires_auth(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 1 plan: без токена → 401 unauthorized."""
    target = await create_user(db_session)

    response = await client.post(f"/admin/users/{target.id}/block")

    assert response.status_code == 401, response.text
    assert_error(response.json(), "unauthorized")


@pytest.mark.parametrize("action", ["block", "unblock"])
async def test_block_unknown_user_not_found(
    client: AsyncClient, db_session: AsyncSession, action: str
) -> None:
    """Step 1 plan: несуществующий id → 404 not_found, аудит не пишется."""
    _, headers = await admin_headers(client, db_session)

    response = await client.post(f"/admin/users/999999/{action}", headers=headers)

    assert response.status_code == 404, response.text
    assert_error(response.json(), "not_found")
    assert list(await db_session.scalars(select(AuditLog))) == []


async def test_admin_cannot_block_self(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-28, решение 2026-10-05: админ не может заблокировать себя; статус и аудит не меняются."""
    admin = await create_user(db_session, role=UserRole.ADMIN)
    headers = auth((await login(client, admin.email))["access_token"])

    response = await client.post(f"/admin/users/{admin.id}/block", headers=headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert (await client.get("/users/me", headers=headers)).json()["status"] == "active"
    audit_count = await db_session.scalar(select(func.count()).select_from(AuditLog))
    assert audit_count == 0
