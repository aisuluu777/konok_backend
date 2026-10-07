from collections.abc import AsyncIterator
from typing import Annotated

from annotated_types import Ge, Le
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


# id из запроса в пределах колонки: иначе asyncpg падает ещё до запроса и получается 500.
SmallId = Annotated[int, Ge(1), Le(2**15 - 1)]  # SMALLSERIAL
BigId = Annotated[int, Ge(1), Le(2**63 - 1)]  # BIGSERIAL


engine: AsyncEngine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Одна сессия на запрос. Транзакциями управляет сервис."""
    async with SessionFactory() as session:
        yield session
