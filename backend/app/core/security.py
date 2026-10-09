import asyncio
import hashlib
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.errors import PermissionDeniedError, UnauthorizedError
from app.core.redis import get_redis
from app.modules.users.models import UserRole

JWT_ALGORITHM = "HS256"

_hasher = PasswordHasher()


# --- Пароли ---------------------------------------------------------------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


# argon2 нагружает CPU — в async-коде только через поток, чтобы не блокировать event loop.
async def hash_password_async(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password: str, password_hash: str) -> bool:
    return await asyncio.to_thread(verify_password, password, password_hash)


_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


async def verify_dummy_password_async(password: str) -> None:
    """Тратит столько же времени, сколько настоящая проверка, — когда пользователя нет."""
    await verify_password_async(password, _DUMMY_HASH)


# --- Access-токен (JWT) ---------------------------------------------------


def create_access_token(user_id: int, role: UserRole, ttl: timedelta) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "role": role.value,
        "type": "access",
        "iat": now,
        "exp": now + ttl,
        "jti": secrets.token_hex(16),  # уникальность: два токена в одну секунду не совпадут
    }
    return jwt.encode(claims, get_settings().secret_key.get_secret_value(), JWT_ALGORITHM)


@dataclass(frozen=True)
class CurrentUser:
    id: int
    role: UserRole


def decode_access_token(token: str) -> CurrentUser:
    try:
        claims = jwt.decode(
            token,
            get_settings().secret_key.get_secret_value(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "exp", "type"]},
        )
        if claims["type"] != "access":
            raise UnauthorizedError("Invalid token")
        return CurrentUser(id=int(claims["sub"]), role=UserRole(claims["role"]))
    except (jwt.InvalidTokenError, KeyError, ValueError) as exc:
        raise UnauthorizedError("Invalid token") from exc


# --- Refresh-токены в Redis -----------------------------------------------
# Токен — случайная строка; в Redis хранится только его sha256.
# auth:refresh:<hash> → user_id (TTL = срок жизни), auth:user_refresh:<user_id> → set хешей.


def _refresh_key(token_hash: str) -> str:
    return f"auth:refresh:{token_hash}"


def _user_refresh_key(user_id: int) -> str:
    return f"auth:user_refresh:{user_id}"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_refresh_token(redis: Redis, user_id: int, ttl: timedelta) -> str:
    token = secrets.token_urlsafe(48)
    token_hash = _hash_token(token)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.set(_refresh_key(token_hash), user_id, ex=ttl)
        pipe.sadd(_user_refresh_key(user_id), token_hash)
        pipe.expire(_user_refresh_key(user_id), ttl)
        await pipe.execute()
    return token


async def consume_refresh_token(redis: Redis, token: str) -> int | None:
    """Атомарно забирает refresh-токен (одноразовый — ротация). None, если его нет."""
    token_hash = _hash_token(token)
    user_id = await redis.getdel(_refresh_key(token_hash))
    if user_id is None:
        return None
    await redis.srem(_user_refresh_key(int(user_id)), token_hash)
    return int(user_id)


async def revoke_all_refresh_tokens(redis: Redis, user_id: int) -> None:
    hashes = await redis.smembers(_user_refresh_key(user_id))
    # Клиент с decode_responses=True отдаёт str; str() — для типизации redis-py (bytes | str).
    keys = [_refresh_key(str(h)) for h in hashes]
    await redis.delete(*keys, _user_refresh_key(user_id))


# --- Денилист заблокированных ---------------------------------------------
# Живой access-токен заблокированного отклоняется. Ключ без TTL: снимается только при
# разблокировке — так он не истечёт раньше токенов, выданных с прежним (большим) TTL
# или выданных в гонке «логин против блокировки».
# ЗАЧЕМ ЭТО, ЕСЛИ МОЖЕТ ПРОСТО ИСТЕКАТЬ, А ВМЕСТО ЭТОГО СДЕЛАТЬ ФУНКЦИЮ ГДЕ ПРОВЕРЯЕ

def _denylist_key(user_id: int) -> str:
    return f"auth:denylist:{user_id}"


async def add_to_denylist(redis: Redis, user_id: int) -> None:
    await redis.set(_denylist_key(user_id), 1)


async def remove_from_denylist(redis: Redis, user_id: int) -> None:
    await redis.delete(_denylist_key(user_id))


# --- Зависимости FastAPI --------------------------------------------------

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> CurrentUser:
    if credentials is None:
        raise UnauthorizedError("Not authenticated")
    user = decode_access_token(credentials.credentials)
    if await redis.exists(_denylist_key(user.id)):
        raise UnauthorizedError("Not authenticated")
    return user


def require_role(*roles: UserRole) -> Callable[..., Awaitable[CurrentUser]]:
    async def dependency(
        user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        if user.role not in roles:
            raise PermissionDeniedError("Insufficient role")
        return user

    return dependency
