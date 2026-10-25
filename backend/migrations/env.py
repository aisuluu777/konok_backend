import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401 — регистрирует все таблицы в Base.metadata
from app.config import get_settings
from app.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations() -> None:
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


asyncio.run(run_migrations())
