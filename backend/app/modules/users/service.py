import secrets
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.audit import write_audit
from app.core.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    PermissionDeniedError,
    TooManyRequestsError,
    UnauthorizedError,
)
from app.core.sms import send_sms
from app.modules.admin.service import get_int_setting
from app.modules.users.models import KycStatus, User, UserStatus
from app.modules.users.schemas import RegisterIn, TokenPair

OTP_MAX_ATTEMPTS = 5


class InvalidCredentialsError(UnauthorizedError):
    code = "invalid_credentials"


class InvalidTokenError(UnauthorizedError):
    code = "invalid_token"


class UserBlockedError(DomainError):
    status_code = 403
    code = "user_blocked"


class InvalidCodeError(DomainError):
    code = "invalid_code"


# --- Токены ---------------------------------------------------------------


async def _access_ttl(session: AsyncSession) -> timedelta:
    return timedelta(minutes=await get_int_setting(session, "access_token_ttl_min"))


async def _issue_tokens(session: AsyncSession, redis: Redis, user: User) -> TokenPair:
    access = security.create_access_token(user.id, user.role, await _access_ttl(session))
    refresh_ttl = timedelta(days=await get_int_setting(session, "refresh_token_ttl_days"))
    refresh = await security.issue_refresh_token(redis, user.id, refresh_ttl)
    return TokenPair(access_token=access, refresh_token=refresh)


# --- Регистрация и вход ---------------------------------------------------


async def register(session: AsyncSession, redis: Redis, data: RegisterIn) -> tuple[User, TokenPair]:
    """Register-then-verify: телефон подтверждается отдельно и пока ничего не гейтит."""
    user = User(
        first_name=data.first_name,
        last_name=data.last_name,
        email=data.email,
        phone=data.phone,
        password_hash=await security.hash_password_async(data.password),
        terms_accepted_at=datetime.now(UTC),
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # asyncpg UniqueViolationError — причина DBAPI-ошибки, в ней имя нарушенного индекса.
        constraint = getattr(exc.orig.__cause__, "constraint_name", None) if exc.orig else None
        if constraint == "users_email_uq":
            raise ConflictError("Email is already registered") from exc
        if constraint == "users_phone_uq":
            raise ConflictError("Phone is already registered") from exc
        raise
    await session.refresh(user)
    return user, await _issue_tokens(session, redis, user)


async def login(session: AsyncSession, redis: Redis, email: str, password: str) -> TokenPair:
    user = await session.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))
    if user is None:
        # Проверка по фиктивному хешу: время ответа не выдаёт, существует ли email.
        await security.verify_dummy_password_async(password)
        raise InvalidCredentialsError("Invalid email or password")
    if not await security.verify_password_async(password, user.password_hash):
        raise InvalidCredentialsError("Invalid email or password")
    if user.status == UserStatus.BLOCKED:
        raise UserBlockedError("User is blocked")
    return await _issue_tokens(session, redis, user)


async def refresh(session: AsyncSession, redis: Redis, refresh_token: str) -> TokenPair:
    user_id = await security.consume_refresh_token(redis, refresh_token)
    if user_id is None:
        raise InvalidTokenError("Invalid refresh token")
    user = await session.get(User, user_id)
    if user is None or user.status == UserStatus.BLOCKED:
        raise InvalidTokenError("Invalid refresh token")
    return await _issue_tokens(session, redis, user)


async def logout(redis: Redis, refresh_token: str) -> None:
    await security.consume_refresh_token(redis, refresh_token)


async def get_user(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found")
    return user


# --- Подтверждение телефона (OTP в Redis) ---------------------------------
# otp:<phone> — hash {code, attempts}, TTL = otp_ttl_sec;
# otp:lock:<phone> — пауза перед повторной отправкой (otp_resend_sec).


def _otp_key(phone: str) -> str:
    return f"otp:{phone}"


def _otp_lock_key(phone: str) -> str:
    return f"otp:lock:{phone}"


# Атомарная проверка кода: попытка засчитывается в том же скрипте, что и сравнение, поэтому
# параллельные запросы не обходят лимит; несуществующий ключ не создаётся заново (без TTL).
# Возвращает 1 — код верный (ключ удалён), 0 — кода нет или он неверный.
_VERIFY_OTP_LUA = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
local attempts = redis.call('HINCRBY', KEYS[1], 'attempts', 1)
if redis.call('HGET', KEYS[1], 'code') == ARGV[1] then
  redis.call('DEL', KEYS[1])
  return 1
end
if attempts >= tonumber(ARGV[2]) then redis.call('DEL', KEYS[1]) end
return 0
"""


async def request_phone_code(session: AsyncSession, redis: Redis, user_id: int) -> int:
    user = await get_user(session, user_id)
    if user.phone_verified_at is not None:
        raise ConflictError("Phone is already verified")

    resend_sec = await get_int_setting(session, "otp_resend_sec")
    if not await redis.set(_otp_lock_key(user.phone), 1, nx=True, ex=resend_sec):
        raise TooManyRequestsError("Code was sent recently, try again later")

    ttl_sec = await get_int_setting(session, "otp_ttl_sec")
    code = f"{secrets.randbelow(10**6):06d}"
    async with redis.pipeline(transaction=True) as pipe:
        pipe.delete(_otp_key(user.phone))
        pipe.hset(_otp_key(user.phone), mapping={"code": code, "attempts": 0})
        pipe.expire(_otp_key(user.phone), ttl_sec)
        await pipe.execute()
    await send_sms(user.phone, f"Konok code: {code}")
    return ttl_sec


async def verify_phone_code(session: AsyncSession, redis: Redis, user_id: int, code: str) -> User:
    user = await get_user(session, user_id)
    matched = await redis.eval(_VERIFY_OTP_LUA, 1, _otp_key(user.phone), code, OTP_MAX_ATTEMPTS)
    if matched != 1:
        raise InvalidCodeError("Code is invalid or expired")

    user.phone_verified_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(user)
    return user


# --- Блокировка пользователей (admin) -------------------------------------


async def set_user_status(
    session: AsyncSession, redis: Redis, *, actor_id: int, user_id: int, status: UserStatus
) -> User:
    """Блокировка/разблокировка с записью в журнал (R-28). Повтор того же статуса — без записи."""
    if actor_id == user_id:
        raise ConflictError("You cannot change your own status")
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise NotFoundError("User not found")
    if user.status != status:
        user.status = status
        action = "user_blocked" if status == UserStatus.BLOCKED else "user_unblocked"
        write_audit(
            session, actor_id=actor_id, action=action, entity_type="user", entity_id=user.id
        )
        await session.commit()
        await session.refresh(user)

    # Redis — после коммита (если коммит упал, доступ не меняется) и всегда, даже при повторе
    # того же статуса: операции идемпотентны, повторный запрос доводит до конца упавшую попытку.
    if status == UserStatus.BLOCKED:
        await security.revoke_all_refresh_tokens(redis, user.id)
        await security.add_to_denylist(redis, user.id)
    else:
        await security.remove_from_denylist(redis, user.id)
    return user


# --- PIN заселения (R-23; хранится открыто — решение MVP, не окончательное) ---


def new_pin() -> str:
    return f"{secrets.randbelow(10**6):06d}"


async def get_pin(session: AsyncSession, user_id: int) -> str:
    pin = await session.scalar(select(User.checkin_pin).where(User.id == user_id))
    if pin is None:
        raise NotFoundError("PIN is issued after KYC approval")
    return pin


async def regenerate_pin(session: AsyncSession, user_id: int) -> str:
    """Новый PIN; старый перестаёт действовать."""
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or user.kyc_status != KycStatus.APPROVED:
        raise PermissionDeniedError("KYC approval required")
    user.checkin_pin = new_pin()
    await session.commit()
    return user.checkin_pin
