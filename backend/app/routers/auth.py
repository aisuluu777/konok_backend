"""Регистрация → код из письма (OTP) → JWT."""

from fastapi import APIRouter, status
from sqlalchemy import select

from app.config import get_settings
from app.deps import RedisDep, Session
from app.errors import AppError, bad_request, conflict
from app.mailer import send_email
from app.models import User
from app.schemas.auth import EmailIn, LoginIn, RefreshIn, RegisterIn, TokenPair, VerifyEmailIn
from app.schemas.common import Message
from app.security import (
    create_access_token,
    create_refresh_token,
    delete_refresh_token,
    generate_code,
    hash_password,
    rate_limit,
    use_refresh_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


async def _send_otp(redis: RedisDep, email: str) -> None:
    # Не чаще раза в минуту и не больше 5 писем в час на один email.
    await rate_limit(redis, f"otp-send-min:{email}", 1, 60)
    await rate_limit(redis, f"otp-send-hour:{email}", 5, 3600)
    code = generate_code()
    await redis.set(f"otp:{email}", code, ex=get_settings().otp_minutes * 60)
    await send_email(email, "Код подтверждения «Конок»", f"Ваш код: {code}")


async def _tokens(redis: RedisDep, user: User) -> TokenPair:
    return TokenPair(
        access_token=create_access_token(user.id),
        refresh_token=await create_refresh_token(redis, user.id),
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(data: RegisterIn, session: Session, redis: RedisDep) -> Message:
    """Создаёт аккаунт и отправляет 6-значный код на email."""
    email = data.email.lower()
    if await session.scalar(select(User.id).where(User.email == email)):
        raise conflict("Пользователь с таким email уже есть")
    session.add(
        User(
            email=email,
            password_hash=hash_password(data.password),
            full_name=data.full_name,
            checkin_pin=generate_code(),
        )
    )
    await session.commit()
    await _send_otp(redis, email)
    return Message(message="Код подтверждения отправлен на email")


@router.post("/verify-email")
async def verify_email(data: VerifyEmailIn, session: Session, redis: RedisDep) -> TokenPair:
    """Подтверждает email кодом из письма и сразу выдаёт токены."""
    email = data.email.lower()
    await rate_limit(redis, f"otp-verify:{email}", 5, get_settings().otp_minutes * 60)
    user = await session.scalar(select(User).where(User.email == email))
    if user is None or await redis.get(f"otp:{email}") != data.code:
        raise bad_request("Неверный или просроченный код")
    await redis.delete(f"otp:{email}")
    user.is_email_confirmed = True
    await session.commit()
    return await _tokens(redis, user)


@router.post("/resend-code", status_code=status.HTTP_202_ACCEPTED)
async def resend_code(data: EmailIn, session: Session, redis: RedisDep) -> Message:
    email = data.email.lower()
    user = await session.scalar(select(User).where(User.email == email))
    if user is not None and not user.is_email_confirmed:
        await _send_otp(redis, email)
    return Message(message="Если аккаунт ждёт подтверждения, код отправлен")


@router.post("/login")
async def login(data: LoginIn, session: Session, redis: RedisDep) -> TokenPair:
    """Не больше 10 попыток входа за 15 минут на один email."""
    await rate_limit(redis, f"login:{data.email.lower()}", 10, 15 * 60)
    user = await session.scalar(select(User).where(User.email == data.email.lower()))
    if user is None or not verify_password(data.password, user.password_hash):
        raise AppError(401, "Неверный email или пароль")
    if not user.is_active:
        raise AppError(403, "Аккаунт заблокирован", code="user_blocked")
    if not user.is_email_confirmed:
        raise AppError(403, "Email не подтверждён", code="email_not_confirmed")
    return await _tokens(redis, user)


@router.post("/refresh")
async def refresh(data: RefreshIn, session: Session, redis: RedisDep) -> TokenPair:
    """Новая пара токенов; старый refresh-токен больше не работает."""
    user_id = await use_refresh_token(redis, data.refresh_token)
    user = await session.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        raise AppError(401, "Недействительный refresh-токен")
    return await _tokens(redis, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(data: RefreshIn, redis: RedisDep) -> None:
    await delete_refresh_token(redis, data.refresh_token)
