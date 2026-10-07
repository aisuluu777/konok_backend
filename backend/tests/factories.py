"""Фабрики тестовых данных и хелперы для auth (Step 1 plan)."""

import itertools
from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.modules.users.models import User, UserRole, UserStatus

DEFAULT_PASSWORD = "Secret123!"

_counter = itertools.count(1)


async def create_user(
    session: AsyncSession,
    *,
    role: UserRole = UserRole.USER,
    status: UserStatus = UserStatus.ACTIVE,
    email: str | None = None,
    phone: str | None = None,
    password: str = DEFAULT_PASSWORD,
    first_name: str = "Test",
    last_name: str = "User",
    phone_verified: bool = False,
) -> User:
    """Вставляет пользователя через ORM и коммитит. Email/телефон по умолчанию уникальны."""
    n = next(_counter)
    now = datetime.now(UTC)
    user = User(
        role=role,
        status=status,
        first_name=first_name,
        last_name=last_name,
        email=email if email is not None else f"user{n}@example.kg",
        phone=phone if phone is not None else f"+996700{n:06d}",
        password_hash=hash_password(password),
        phone_verified_at=now if phone_verified else None,
        terms_accepted_at=now,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def login(
    client: AsyncClient, email: str, password: str = DEFAULT_PASSWORD
) -> dict[str, str]:
    """Логинится через API и возвращает пару токенов; падает с понятным сообщением при ошибке."""
    response = await client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, f"login failed: {response.status_code} {response.text}"
    data: dict[str, str] = response.json()
    return data


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
