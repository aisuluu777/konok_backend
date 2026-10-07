"""Регистрация, логин, refresh/logout, /users/me (Step 1 plan; R-01)."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import User, UserStatus
from tests.factories import DEFAULT_PASSWORD, auth, create_user, login


def register_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "first_name": "Айгуль",
        "last_name": "Асанова",
        "email": "aigul@example.kg",
        "phone": "+996555123456",
        "password": "Secret123!",
        "terms_accepted": True,
    }
    payload.update(overrides)
    return payload


def assert_error(response_json: dict[str, Any], code: str) -> None:
    assert "error" in response_json, response_json
    assert response_json["error"]["code"] == code
    assert isinstance(response_json["error"]["message"], str)


# ---------------------------------------------------------------- register


async def test_register_creates_user_with_terms_and_unverified_phone(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 1 plan, R-01: регистрация ставит terms_accepted_at, phone_verified_at = null,
    роль user, kyc not_started и сразу выдаёт пару токенов."""
    response = await client.post("/auth/register", json=register_payload())

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    user = body["user"]
    assert user["role"] == "user"
    assert user["status"] == "active"
    assert user["kyc_status"] == "not_started"
    assert user["phone_verified_at"] is None
    assert user["email"] == "aigul@example.kg"
    assert user["phone"] == "+996555123456"
    assert user["first_name"] == "Айгуль"
    assert "password" not in user
    assert "password_hash" not in user

    db_user = await db_session.scalar(select(User).where(User.id == user["id"]))
    assert db_user is not None
    assert db_user.terms_accepted_at is not None
    assert db_user.phone_verified_at is None
    assert db_user.password_hash != "Secret123!"


async def test_register_access_token_works_for_users_me(client: AsyncClient) -> None:
    """Step 1 plan: access-токен из регистрации сразу проходит /users/me."""
    response = await client.post("/auth/register", json=register_payload())
    assert response.status_code == 201, response.text
    body = response.json()

    me = await client.get("/users/me", headers=auth(body["access_token"]))

    assert me.status_code == 200, me.text
    assert me.json()["id"] == body["user"]["id"]


@pytest.mark.parametrize("terms", [False, None])
async def test_register_requires_terms_accepted(client: AsyncClient, terms: bool | None) -> None:
    """Step 1 plan: без terms_accepted=true регистрация отклоняется (false или поле отсутствует)."""
    payload = register_payload(terms_accepted=terms)
    if terms is None:
        del payload["terms_accepted"]

    response = await client.post("/auth/register", json=payload)

    assert response.status_code == 422, response.text
    assert_error(response.json(), "validation_error")


async def test_register_email_unique_case_insensitive(client: AsyncClient) -> None:
    """Step 1 plan: email уникален без учёта регистра."""
    first = await client.post("/auth/register", json=register_payload(email="a@x.kg"))
    assert first.status_code == 201, first.text

    second = await client.post(
        "/auth/register", json=register_payload(email="A@x.kg", phone="+996555000002")
    )

    assert second.status_code == 409, second.text
    assert_error(second.json(), "conflict")


async def test_register_email_stored_as_entered(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 1 plan (граница): email хранится как введён, без приведения к нижнему регистру."""
    response = await client.post("/auth/register", json=register_payload(email="Mixed@Case.KG"))

    assert response.status_code == 201, response.text
    assert response.json()["user"]["email"] == "Mixed@Case.KG"
    db_email = await db_session.scalar(select(User.email))
    assert db_email == "Mixed@Case.KG"


async def test_register_phone_unique(client: AsyncClient) -> None:
    """Step 1 plan: телефон уникален."""
    first = await client.post("/auth/register", json=register_payload())
    assert first.status_code == 201, first.text

    second = await client.post("/auth/register", json=register_payload(email="other@example.kg"))

    assert second.status_code == 409, second.text
    assert_error(second.json(), "conflict")


async def test_register_password_min_length_boundary(client: AsyncClient) -> None:
    """Step 1 plan (граница): пароль из 8 символов принимается, из 7 — нет."""
    short = await client.post("/auth/register", json=register_payload(password="1234567"))
    assert short.status_code == 422, short.text
    assert_error(short.json(), "validation_error")

    exact = await client.post("/auth/register", json=register_payload(password="12345678"))
    assert exact.status_code == 201, exact.text


@pytest.mark.parametrize(
    "phone", ["0555123456", "996555123456", "+0555123456", "+9965", "+99655512345a"]
)
async def test_register_rejects_non_e164_phone(client: AsyncClient, phone: str) -> None:
    """Step 1 plan: телефон в формате E.164 (^\\+[1-9]\\d{7,14}$)."""
    response = await client.post("/auth/register", json=register_payload(phone=phone))

    assert response.status_code == 422, response.text
    assert_error(response.json(), "validation_error")


@pytest.mark.parametrize("phone", ["+12345678", "+123456789012345"])
async def test_register_accepts_e164_length_bounds(client: AsyncClient, phone: str) -> None:
    """Step 1 plan (граница): E.164 от 8 до 15 цифр после «+» принимается."""
    response = await client.post("/auth/register", json=register_payload(phone=phone))

    assert response.status_code == 201, response.text


@pytest.mark.parametrize("phone", ["+1234567", "+1234567890123456"])
async def test_register_rejects_e164_out_of_length_bounds(client: AsyncClient, phone: str) -> None:
    """Step 1 plan (граница): 7 и 16 цифр после «+» — вне E.164."""
    response = await client.post("/auth/register", json=register_payload(phone=phone))

    assert response.status_code == 422, response.text
    assert_error(response.json(), "validation_error")


@pytest.mark.parametrize("email", ["no-at-sign.kg", "@example.kg", "user@", "user@localhost"])
async def test_register_rejects_invalid_email(client: AsyncClient, email: str) -> None:
    """Step 1 plan: email содержит «@» с непустыми частями и точку в домене."""
    response = await client.post("/auth/register", json=register_payload(email=email))

    assert response.status_code == 422, response.text
    assert_error(response.json(), "validation_error")


# ---------------------------------------------------------------- login


async def test_login_returns_token_pair(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 1 plan: login с верным паролем выдаёт access + refresh."""
    user = await create_user(db_session, email="a@x.kg")

    response = await client.post(
        "/auth/login", json={"email": user.email, "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]


async def test_login_email_case_insensitive(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 1 plan (граница): логин по email в другом регистре проходит."""
    await create_user(db_session, email="a@x.kg")

    response = await client.post(
        "/auth/login", json={"email": "A@X.KG", "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 200, response.text


async def test_login_wrong_password_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 1 plan: неверный пароль → 401 invalid_credentials."""
    user = await create_user(db_session)

    response = await client.post("/auth/login", json={"email": user.email, "password": "Wrong123!"})

    assert response.status_code == 401, response.text
    assert_error(response.json(), "invalid_credentials")


async def test_login_unknown_email_same_error(client: AsyncClient) -> None:
    """Step 1 plan: несуществующий email неотличим от неверного пароля."""
    response = await client.post(
        "/auth/login", json={"email": "ghost@example.kg", "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 401, response.text
    assert_error(response.json(), "invalid_credentials")


async def test_login_blocked_user_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 1 plan: заблокированный с верным паролем → 403 user_blocked."""
    user = await create_user(db_session, status=UserStatus.BLOCKED)

    response = await client.post(
        "/auth/login", json={"email": user.email, "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 403, response.text
    assert_error(response.json(), "user_blocked")


async def test_login_blocked_user_wrong_password_is_invalid_credentials(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 1 plan (граница): неверный пароль заблокированного не раскрывает статус блокировки.

    Контракт: user_blocked — только при верном пароле.
    """
    user = await create_user(db_session, status=UserStatus.BLOCKED)

    response = await client.post("/auth/login", json={"email": user.email, "password": "Wrong123!"})

    assert response.status_code == 401, response.text
    assert_error(response.json(), "invalid_credentials")


# ---------------------------------------------------------------- refresh / logout


async def test_refresh_rotates_both_tokens(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 1 plan: refresh выдаёт новую пару, оба токена новые, новый access работает."""
    user = await create_user(db_session)
    tokens = await login(client, user.email)

    response = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    assert response.status_code == 200, response.text
    new = response.json()
    assert new["token_type"] == "bearer"
    assert new["refresh_token"] != tokens["refresh_token"]
    assert new["access_token"] != tokens["access_token"]
    me = await client.get("/users/me", headers=auth(new["access_token"]))
    assert me.status_code == 200, me.text


async def test_refresh_old_token_reuse_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 1 plan: ротация — использованный refresh-токен повторно не принимается."""
    user = await create_user(db_session)
    tokens = await login(client, user.email)
    first = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert first.status_code == 200, first.text

    reuse = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    assert reuse.status_code == 401, reuse.text
    assert_error(reuse.json(), "invalid_token")


async def test_refresh_rotated_token_works_again(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 1 plan (граница): цепочка ротаций — выданный на refresh токен сам годен для refresh."""
    user = await create_user(db_session)
    tokens = await login(client, user.email)
    first = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert first.status_code == 200, first.text

    second = await client.post(
        "/auth/refresh", json={"refresh_token": first.json()["refresh_token"]}
    )

    assert second.status_code == 200, second.text


async def test_refresh_garbage_token_rejected(client: AsyncClient) -> None:
    """Step 1 plan: мусорный refresh-токен → 401 invalid_token."""
    response = await client.post("/auth/refresh", json={"refresh_token": "not-a-token"})

    assert response.status_code == 401, response.text
    assert_error(response.json(), "invalid_token")


async def test_refresh_with_access_token_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 1 plan (граница): access-токен не годится как refresh."""
    user = await create_user(db_session)
    tokens = await login(client, user.email)

    response = await client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]})

    assert response.status_code == 401, response.text
    assert_error(response.json(), "invalid_token")


async def test_logout_revokes_refresh_token(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 1 plan: после logout refresh с этим токеном → 401 invalid_token."""
    user = await create_user(db_session)
    tokens = await login(client, user.email)

    logout = await client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert logout.status_code == 204, logout.text

    response = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 401, response.text
    assert_error(response.json(), "invalid_token")


async def test_logout_revokes_only_its_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 1 plan (граница): logout одной сессии не трогает refresh другой сессии того же юзера."""
    user = await create_user(db_session)
    first = await login(client, user.email)
    second = await login(client, user.email)

    logout = await client.post("/auth/logout", json={"refresh_token": first["refresh_token"]})
    assert logout.status_code == 204, logout.text

    response = await client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------- /users/me


async def test_users_me_returns_current_user(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 1 plan, R-01: /users/me отдаёт UserOut текущего пользователя."""
    user = await create_user(db_session, first_name="Бакыт", phone_verified=True)
    tokens = await login(client, user.email)

    response = await client.get("/users/me", headers=auth(tokens["access_token"]))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == user.id
    assert body["role"] == "user"
    assert body["status"] == "active"
    assert body["first_name"] == "Бакыт"
    assert body["last_name"] == user.last_name
    assert body["email"] == user.email
    assert body["phone"] == user.phone
    assert body["phone_verified_at"] is not None
    assert body["kyc_status"] == "not_started"
    assert body["created_at"]
    assert "password_hash" not in body


async def test_users_me_without_token(client: AsyncClient) -> None:
    """Step 1 plan: без заголовка Authorization → 401 unauthorized."""
    response = await client.get("/users/me")

    assert response.status_code == 401, response.text
    assert_error(response.json(), "unauthorized")


async def test_users_me_garbage_token(client: AsyncClient) -> None:
    """Step 1 plan: мусорный токен → 401 unauthorized."""
    response = await client.get("/users/me", headers=auth("garbage.token.value"))

    assert response.status_code == 401, response.text
    assert_error(response.json(), "unauthorized")


async def test_users_me_rejects_refresh_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 1 plan (граница): refresh-токен вместо access → 401."""
    user = await create_user(db_session)
    tokens = await login(client, user.email)

    response = await client.get("/users/me", headers=auth(tokens["refresh_token"]))

    assert response.status_code == 401, response.text
    assert_error(response.json(), "unauthorized")
