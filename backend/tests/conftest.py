import atexit
import os
import shutil
import tempfile

# До импорта app: тесты всегда работают с тестовыми БД и Redis, даже если в .env или в shell
# указаны рабочие. Обе переменные присваиваются жёстко, а не через setdefault.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://konok:konok@localhost:5433/konok_test"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["REDIS_URL"] = TEST_REDIS_URL
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
# Загруженные в тестах файлы — во временную папку, не в рабочий storage/.
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="konok-test-storage-")
atexit.register(shutil.rmtree, os.environ["STORAGE_DIR"], ignore_errors=True)

import asyncio  # noqa: E402
from collections.abc import AsyncIterator, Callable  # noqa: E402
from contextlib import AbstractAsyncContextManager  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402
from urllib.parse import urlparse  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from redis.asyncio import Redis  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402

import app.models  # noqa: E402, F401  — все модели в Base.metadata
from app.core.db import Base, get_session  # noqa: E402
from app.core.redis import get_redis  # noqa: E402
from app.main import create_app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# Таблицы с сидами из миграций. TRUNCATE ... CASCADE очищает и их (settings.updated_by → users),
# поэтому после каждого теста их содержимое восстанавливается из снимка, сделанного после миграций.
# Заодно откатываются изменения справочников и настроек, сделанные тестом.
# Порядок важен: родительские таблицы раньше (places → regions).
SEEDED_TABLES = ("settings", "regions", "tags", "places")

SeedSnapshot = dict[str, list[dict[str, Any]]]

# Защита: тесты сносят схему и чистят таблицы и Redis — только на тестовых экземплярах.
if not (make_url(TEST_DATABASE_URL).database or "").endswith("_test"):
    pytest.exit(f"TEST_DATABASE_URL должен указывать на БД *_test, сейчас: {TEST_DATABASE_URL}")
if urlparse(TEST_REDIS_URL).path.lstrip("/") in ("", "0"):
    pytest.exit(f"TEST_REDIS_URL должен указывать на отдельную БД Redis (не 0): {TEST_REDIS_URL}")


async def _reset_schema() -> None:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


async def _snapshot_seeds() -> SeedSnapshot:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with engine.connect() as conn:
        snapshot = {
            name: [
                dict(row)
                for row in (await conn.execute(Base.metadata.tables[name].select())).mappings()
            ]
            for name in SEEDED_TABLES
        }
    await engine.dispose()
    return snapshot


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> SeedSnapshot:
    """Пересоздаёт схему db-test, накатывает реальные миграции (расширения, EXCLUDE)
    и возвращает снимок сидов.

    Фикстура синхронная: Alembic env.py вызывает asyncio.run, внутри event loop это упадёт.
    """
    asyncio.run(_reset_schema())
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["database_url"] = TEST_DATABASE_URL
    command.upgrade(cfg, "head")
    return asyncio.run(_snapshot_seeds())


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    # NullPool: у каждого теста свой event loop, соединения между тестами не переиспользуются.
    eng = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield eng
    await eng.dispose()


@pytest.fixture(autouse=True)
async def clean_tables(engine: AsyncEngine, migrated_db: SeedSnapshot) -> AsyncIterator[None]:
    """После каждого теста очищает все таблицы и возвращает сиды: тесты коммитят по-настоящему."""
    yield
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        for name in SEEDED_TABLES:
            if migrated_db[name]:
                await conn.execute(Base.metadata.tables[name].insert(), migrated_db[name])
            if "id" in Base.metadata.tables[name].c:
                # RESTART IDENTITY сбросил счётчик, а снимок вставлен с явными id — подвинуть.
                await conn.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), "
                        f"COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {name}"
                    )
                )


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def make_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[], AbstractAsyncContextManager[AsyncSession]]:
    """Открывает независимую сессию (своё соединение) — для тестов конкурентности."""
    return session_factory


@pytest.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    # Свой клиент на тест: глобальный redis_client привязан к первому event loop.
    client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession], redis: Redis
) -> AsyncIterator[AsyncClient]:
    app = create_app()

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def override_get_redis() -> Redis:
        return redis

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_redis] = override_get_redis
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
