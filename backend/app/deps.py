from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.payments import PaymentProvider, get_payment_provider
from app.redis import get_redis

Session = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
Provider = Annotated[PaymentProvider, Depends(get_payment_provider)]
