"""Устойчивость auth к гонкам и поддельным токенам (Step 1 plan, ревью)."""

import asyncio
from datetime import UTC, datetime, timedelta

import jwt
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.config import get_settings
from app.modules.users.models import UserRole
from tests.factories import auth, create_user, login


def _jwt(claims: dict[str, object], key: str | None = None, algorithm: str = "HS256") -> str:
    secret = key if key is not None else get_settings().secret_key.get_secret_value()
    return jwt.encode(claims, secret, algorithm)


def _claims(user_id: int, **overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": str(user_id),
        "role": "user",
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return claims


async def test_parallel_refresh_of_same_token_succeeds_once(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Refresh-токен одноразовый даже при параллельных запросах (ротация)."""
    user = await create_user(db_session)
    tokens = await login(client, user.email)

    responses = await asyncio.gather(
        *(
            client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
            for _ in range(5)
        )
    )

    assert sorted(r.status_code for r in responses) == [200, 401, 401, 401, 401]


async def test_access_token_validation(client: AsyncClient, db_session: AsyncSession) -> None:
    """Принимается только наш HS256 access-токен с неистёкшим exp."""
    user = await create_user(db_session)
    valid = _jwt(_claims(user.id))
    rejected = {
        "expired": _jwt(_claims(user.id, exp=datetime.now(UTC) - timedelta(seconds=1))),
        "foreign_signature": _jwt(_claims(user.id), key="another-secret-key-32-bytes-long!!"),
        "alg_none": jwt.encode(_claims(user.id), None, algorithm="none"),
        "refresh_type": _jwt(_claims(user.id, type="refresh")),
        "no_exp": _jwt({k: v for k, v in _claims(user.id).items() if k != "exp"}),
    }

    assert (await client.get("/users/me", headers=auth(valid))).status_code == 200
    for name, token in rejected.items():
        response = await client.get("/users/me", headers=auth(token))
        assert response.status_code == 401, name
        assert response.json()["error"]["code"] == "unauthorized", name


async def test_parallel_wrong_otp_attempts_cannot_exceed_limit(
    client: AsyncClient, db_session: AsyncSession, redis: Redis
) -> None:
    """Параллельные неверные коды не обходят лимит 5 попыток и не оставляют ключ без TTL."""
    user = await create_user(db_session)
    headers = auth((await login(client, user.email))["access_token"])
    assert (await client.post("/auth/phone/request-code", headers=headers)).status_code == 202
    code = await redis.hget(f"otp:{user.phone}", "code")
    wrong = "000000" if code != "000000" else "111111"

    await asyncio.gather(
        *(
            client.post("/auth/phone/verify", headers=headers, json={"code": wrong})
            for _ in range(10)
        )
    )
    response = await client.post("/auth/phone/verify", headers=headers, json={"code": code})

    assert response.status_code == 400
    assert await redis.exists(f"otp:{user.phone}") == 0


async def test_repeat_block_completes_redis_side_after_failure(
    client: AsyncClient, db_session: AsyncSession, redis: Redis
) -> None:
    """R-28: если Redis-часть блокировки не прошла, повторный block её доводит, без 2-й записи."""
    admin = await create_user(db_session, role=UserRole.ADMIN)
    target = await create_user(db_session)
    admin_headers = auth((await login(client, admin.email))["access_token"])
    target_headers = auth((await login(client, target.email))["access_token"])

    assert (
        await client.post(f"/admin/users/{target.id}/block", headers=admin_headers)
    ).status_code == 200
    # Имитируем сбой Redis после коммита: денилист не поставлен.
    await redis.delete(f"auth:denylist:{target.id}")
    assert (await client.get("/users/me", headers=target_headers)).status_code == 200

    assert (
        await client.post(f"/admin/users/{target.id}/block", headers=admin_headers)
    ).status_code == 200

    assert (await client.get("/users/me", headers=target_headers)).status_code == 401
    audit_count = await db_session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "user_blocked")
    )
    assert audit_count == 1


async def test_denylist_has_no_ttl(
    client: AsyncClient, db_session: AsyncSession, redis: Redis
) -> None:
    """Денилист снимается только разблокировкой, а не истечением TTL."""
    admin = await create_user(db_session, role=UserRole.ADMIN)
    target = await create_user(db_session)
    admin_headers = auth((await login(client, admin.email))["access_token"])

    await client.post(f"/admin/users/{target.id}/block", headers=admin_headers)
    assert await redis.ttl(f"auth:denylist:{target.id}") == -1

    await client.post(f"/admin/users/{target.id}/unblock", headers=admin_headers)
    assert await redis.exists(f"auth:denylist:{target.id}") == 0
