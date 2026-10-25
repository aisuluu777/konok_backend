from collections.abc import AsyncIterator
from enum import StrEnum

from sqlalchemy import Enum, MetaData
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    # Предсказуемые имена ограничений — чтобы миграции могли на них ссылаться.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def str_enum(enum: type[StrEnum]) -> Enum:
    """Enum в БД как VARCHAR + CHECK по значениям (без нативного типа PostgreSQL)."""
    return Enum(
        enum,
        name=enum.__name__.lower(),
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session
