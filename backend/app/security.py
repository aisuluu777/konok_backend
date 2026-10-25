import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.errors import AppError
from app.models import User

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except Argon2Error:
        return False


async def rate_limit(redis: Redis, key: str, limit: int, seconds: int) -> None:
    """Не больше limit запросов за seconds секунд по ключу; иначе 429."""
    count = await redis.incr(f"rate:{key}")
    if count == 1:
        await redis.expire(f"rate:{key}", seconds)
    if count > limit:
        raise AppError(429, "Слишком много попыток. Попробуйте позже")


def generate_code(digits: int = 6) -> str:
    """Случайный цифровой код: OTP и PIN заселения."""
    return f"{secrets.randbelow(10**digits):0{digits}d}"


def create_access_token(user_id: int) -> str:
    settings = get_settings()
    exp = datetime.now(UTC) + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode({"sub": str(user_id), "exp": exp}, settings.secret_key, "HS256")


# Refresh-токен — случайная строка; в Redis: refresh:<токен> → id пользователя.
async def create_refresh_token(redis: Redis, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    ttl = timedelta(days=get_settings().refresh_token_days)
    await redis.set(f"refresh:{token}", user_id, ex=ttl)
    return token


async def use_refresh_token(redis: Redis, token: str) -> int | None:
    """Одноразовый: при обновлении старый токен удаляется."""
    user_id = await redis.getdel(f"refresh:{token}")
    return int(user_id) if user_id else None


async def delete_refresh_token(redis: Redis, token: str) -> None:
    await redis.delete(f"refresh:{token}")


_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    if credentials is None:
        raise AppError(401, "Нужна авторизация")
    try:
        claims = jwt.decode(credentials.credentials, get_settings().secret_key, ["HS256"])
        user_id = int(claims["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError) as exc:
        raise AppError(401, "Недействительный токен") from exc
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise AppError(401, "Пользователь не найден или заблокирован")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
