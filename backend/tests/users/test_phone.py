"""Подтверждение телефона через OTP в Redis (Step 1 plan)."""

from typing import Any

from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import User
from tests.factories import auth, create_user, login

MAX_ATTEMPTS = 5


def assert_error(response_json: dict[str, Any], code: str) -> None:
    assert "error" in response_json, response_json
    assert response_json["error"]["code"] == code


async def logged_in(client: AsyncClient, db_session: AsyncSession) -> tuple[User, dict[str, str]]:
    user = await create_user(db_session)
    tokens = await login(client, user.email)
    return user, auth(tokens["access_token"])


async def request_code(
    client: AsyncClient, headers: dict[str, str], redis: Redis, phone: str
) -> str:
    response = await client.post("/auth/phone/request-code", headers=headers)
    assert response.status_code == 202, response.text
    code = await redis.hget(f"otp:{phone}", "code")
    assert code is not None, "OTP-код не найден в Redis по ключу otp:{phone}"
    return str(code)


def wrong_code(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


async def test_request_code_stores_otp_in_redis(
    client: AsyncClient, db_session: AsyncSession, redis: Redis
) -> None:
    """Step 1 plan: request-code → 202, 6-значный код в Redis с TTL ≈ otp_ttl_sec (300)."""
    user, headers = await logged_in(client, db_session)

    response = await client.post("/auth/phone/request-code", headers=headers)

    assert response.status_code == 202, response.text
    assert response.json() == {"expires_in": 300}
    key = f"otp:{user.phone}"
    code = await redis.hget(key, "code")
    assert code is not None
    assert len(code) == 6
    assert code.isdigit()
    ttl = await redis.ttl(key)
    assert 290 <= ttl <= 300


async def test_request_code_resend_lock(
    client: AsyncClient, db_session: AsyncSession, redis: Redis
) -> None:
    """Step 1 plan: повторный запрос кода раньше otp_resend_sec (45 с) → 429."""
    user, headers = await logged_in(client, db_session)
    first_code = await request_code(client, headers, redis, user.phone)

    second = await client.post("/auth/phone/request-code", headers=headers)

    assert second.status_code == 429, second.text
    assert_error(second.json(), "too_many_requests")
    # Отклонённый повтор не перезаписывает действующий код.
    assert await redis.hget(f"otp:{user.phone}", "code") == first_code


async def test_request_code_lock_is_per_user(
    client: AsyncClient, db_session: AsyncSession, redis: Redis
) -> None:
    """Step 1 plan (граница): лок повторной отправки у каждого телефона свой."""
    first_user, first_headers = await logged_in(client, db_session)
    await request_code(client, first_headers, redis, first_user.phone)
    second_user, second_headers = await logged_in(client, db_session)

    response = await client.post("/auth/phone/request-code", headers=second_headers)

    assert response.status_code == 202, response.text
    assert await redis.hget(f"otp:{second_user.phone}", "code") is not None


async def test_request_code_already_verified(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 1 plan: телефон уже подтверждён → 409 conflict."""
    user = await create_user(db_session, phone_verified=True)
    tokens = await login(client, user.email)

    response = await client.post("/auth/phone/request-code", headers=auth(tokens["access_token"]))

    assert response.status_code == 409, response.text
    assert_error(response.json(), "conflict")


async def test_request_code_requires_auth(client: AsyncClient) -> None:
    """Step 1 plan: без access-токена → 401 unauthorized."""
    response = await client.post("/auth/phone/request-code")

    assert response.status_code == 401, response.text
    assert_error(response.json(), "unauthorized")


async def test_verify_correct_code(
    client: AsyncClient, db_session: AsyncSession, redis: Redis
) -> None:
    """Step 1 plan: верный код → phone_verified_at проставлен, ключ OTP удалён."""
    user, headers = await logged_in(client, db_session)
    code = await request_code(client, headers, redis, user.phone)

    response = await client.post("/auth/phone/verify", headers=headers, json={"code": code})

    assert response.status_code == 200, response.text
    assert response.json()["id"] == user.id
    assert response.json()["phone_verified_at"] is not None
    assert await redis.exists(f"otp:{user.phone}") == 0
    verified_at = await db_session.scalar(
        select(User.phone_verified_at)
        .where(User.id == user.id)
        .execution_options(populate_existing=True)
    )
    assert verified_at is not None


async def test_verify_wrong_code_increments_attempts(
    client: AsyncClient, db_session: AsyncSession, redis: Redis
) -> None:
    """Step 1 plan: неверный код → 400 invalid_code, attempts растёт, телефон не подтверждён."""
    user, headers = await logged_in(client, db_session)
    code = await request_code(client, headers, redis, user.phone)

    response = await client.post(
        "/auth/phone/verify", headers=headers, json={"code": wrong_code(code)}
    )

    assert response.status_code == 400, response.text
    assert_error(response.json(), "invalid_code")
    assert await redis.hget(f"otp:{user.phone}", "attempts") == "1"
    me = await client.get("/users/me", headers=headers)
    assert me.json()["phone_verified_at"] is None


async def test_verify_correct_code_after_four_wrong_attempts(
    client: AsyncClient, db_session: AsyncSession, redis: Redis
) -> None:
    """Step 1 plan (граница): 4 неверные попытки — код ещё жив, верный код проходит."""
    user, headers = await logged_in(client, db_session)
    code = await request_code(client, headers, redis, user.phone)
    for _ in range(MAX_ATTEMPTS - 1):
        bad = await client.post(
            "/auth/phone/verify", headers=headers, json={"code": wrong_code(code)}
        )
        assert bad.status_code == 400, bad.text

    response = await client.post("/auth/phone/verify", headers=headers, json={"code": code})

    assert response.status_code == 200, response.text
    assert response.json()["phone_verified_at"] is not None


async def test_verify_code_burned_after_five_wrong_attempts(
    client: AsyncClient, db_session: AsyncSession, redis: Redis
) -> None:
    """Step 1 plan: после 5 неверных попыток код удалён — даже верный → 400 invalid_code."""
    user, headers = await logged_in(client, db_session)
    code = await request_code(client, headers, redis, user.phone)
    for _ in range(MAX_ATTEMPTS):
        bad = await client.post(
            "/auth/phone/verify", headers=headers, json={"code": wrong_code(code)}
        )
        assert bad.status_code == 400, bad.text

    assert await redis.exists(f"otp:{user.phone}") == 0
    response = await client.post("/auth/phone/verify", headers=headers, json={"code": code})

    assert response.status_code == 400, response.text
    assert_error(response.json(), "invalid_code")


async def test_verify_without_requested_code(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 1 plan: код не запрашивался → 400 invalid_code."""
    _, headers = await logged_in(client, db_session)

    response = await client.post("/auth/phone/verify", headers=headers, json={"code": "123456"})

    assert response.status_code == 400, response.text
    assert_error(response.json(), "invalid_code")
