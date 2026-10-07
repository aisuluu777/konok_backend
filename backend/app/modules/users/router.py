from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BigId, get_session
from app.core.redis import get_redis
from app.core.security import CurrentUser, get_current_user, require_role
from app.modules.users import service
from app.modules.users.models import UserRole, UserStatus
from app.modules.users.schemas import (
    LoginIn,
    OtpRequestedOut,
    PinOut,
    RefreshIn,
    RegisterIn,
    RegisterOut,
    TokenPair,
    UserOut,
    VerifyPhoneIn,
)

Session = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
Me = Annotated[CurrentUser, Depends(get_current_user)]
Admin = Annotated[CurrentUser, Depends(require_role(UserRole.ADMIN))]

auth_router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])
admin_users_router = APIRouter(prefix="/admin/users", tags=["admin"])


@auth_router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(data: RegisterIn, session: Session, redis: RedisDep) -> RegisterOut:
    user, tokens = await service.register(session, redis, data)
    return RegisterOut(user=UserOut.model_validate(user), **tokens.model_dump())


@auth_router.post("/login")
async def login(data: LoginIn, session: Session, redis: RedisDep) -> TokenPair:
    return await service.login(session, redis, data.email, data.password)


@auth_router.post("/refresh")
async def refresh(data: RefreshIn, session: Session, redis: RedisDep) -> TokenPair:
    return await service.refresh(session, redis, data.refresh_token)


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(data: RefreshIn, redis: RedisDep) -> None:
    await service.logout(redis, data.refresh_token)


@auth_router.post("/phone/request-code", status_code=status.HTTP_202_ACCEPTED)
async def request_phone_code(me: Me, session: Session, redis: RedisDep) -> OtpRequestedOut:
    expires_in = await service.request_phone_code(session, redis, me.id)
    return OtpRequestedOut(expires_in=expires_in)


@auth_router.post("/phone/verify")
async def verify_phone(data: VerifyPhoneIn, me: Me, session: Session, redis: RedisDep) -> UserOut:
    user = await service.verify_phone_code(session, redis, me.id, data.code)
    return UserOut.model_validate(user)


@users_router.get("/me/pin")
async def get_my_pin(me: Me, session: Session, response: Response) -> PinOut:
    response.headers["Cache-Control"] = "no-store"
    return PinOut(pin=await service.get_pin(session, me.id))


@users_router.post("/me/pin/regenerate")
async def regenerate_my_pin(me: Me, session: Session, response: Response) -> PinOut:
    response.headers["Cache-Control"] = "no-store"
    return PinOut(pin=await service.regenerate_pin(session, me.id))


@users_router.get("/me")
async def get_me(me: Me, session: Session) -> UserOut:
    return UserOut.model_validate(await service.get_user(session, me.id))


@admin_users_router.post("/{user_id}/block")
async def block_user(user_id: BigId, admin: Admin, session: Session, redis: RedisDep) -> UserOut:
    user = await service.set_user_status(
        session, redis, actor_id=admin.id, user_id=user_id, status=UserStatus.BLOCKED
    )
    return UserOut.model_validate(user)


@admin_users_router.post("/{user_id}/unblock")
async def unblock_user(user_id: BigId, admin: Admin, session: Session, redis: RedisDep) -> UserOut:
    user = await service.set_user_status(
        session, redis, actor_id=admin.id, user_id=user_id, status=UserStatus.ACTIVE
    )
    return UserOut.model_validate(user)
