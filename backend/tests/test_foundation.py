import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import engine
from app.core.errors import ConflictError, register_error_handlers


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_btree_gist_extension_installed(db_session: AsyncSession) -> None:
    """Миграция 0001 ставит btree_gist — без него не создать EXCLUDE для броней."""
    result = await db_session.execute(
        text("SELECT 1 FROM pg_extension WHERE extname = 'btree_gist'")
    )

    assert result.scalar_one_or_none() == 1


async def test_two_sessions_use_separate_connections(
    make_session: Callable[[], AbstractAsyncContextManager[AsyncSession]],
) -> None:
    """Харнесс для тестов конкурентности: две сессии — два разных backend-процесса Postgres."""
    query = text("SELECT pg_backend_pid()")
    async with make_session() as first, make_session() as second:
        pid_first, pid_second = await asyncio.gather(first.scalar(query), second.scalar(query))

    assert pid_first != pid_second


async def test_domain_error_translated_to_http() -> None:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise ConflictError("dates taken")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/boom")

    assert response.status_code == 409
    assert response.json() == {"error": {"code": "conflict", "message": "dates taken"}}


async def test_unknown_route_uses_common_error_format(client: AsyncClient) -> None:
    response = await client.get("/no-such-route")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_error"


async def test_validation_error_uses_common_error_format() -> None:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/items/{item_id}")
    async def item(item_id: int) -> dict[str, int]:
        return {"id": item_id}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/items/abc")

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"] == ["path", "item_id"]


async def test_unhandled_error_hides_details() -> None:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/crash")
    async def crash() -> None:
        raise RuntimeError("secret internals")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/crash")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Internal server error"}
    }


async def test_redis_fixture_is_clean_and_usable(redis: Redis) -> None:
    """Каждый тест получает пустую тестовую БД Redis со своим клиентом (свой event loop)."""
    assert await redis.dbsize() == 0
    await redis.set("probe", "1")
    assert await redis.get("probe") == "1"


async def test_redis_fixture_flushed_between_tests(redis: Redis) -> None:
    assert await redis.get("probe") is None


async def test_seeded_settings_changed_by_a_test(db_session: AsyncSession) -> None:
    """Тест меняет сид-настройку — следующий тест должен увидеть исходное значение."""
    await db_session.execute(
        text("UPDATE settings SET value = '999' WHERE key = 'access_token_ttl_min'")
    )
    await db_session.commit()


async def test_seeded_settings_restored_after_cleanup(db_session: AsyncSession) -> None:
    """Сиды settings переживают очистку таблиц (TRUNCATE ... CASCADE через users)."""
    result = await db_session.execute(text("SELECT key, value FROM settings"))
    rows: dict[str, str] = {key: value for key, value in result.all()}

    assert rows == {
        "access_token_ttl_min": "15",
        "refresh_token_ttl_days": "30",
        "otp_ttl_sec": "300",
        "otp_resend_sec": "45",
        "hold_minutes": "15",
        "host_ack_reminder_hours": "24",
        "checkin_time": "13:00",
        "checkout_time": "11:00",
        "commission_rate": "0.1000",
        "kyc_resubmit_hours": "1",
    }


async def test_db_errors_hide_parameters() -> None:
    """Текст ошибки БД без значений параметров: SQLAdmin показывает его в форме (R-41 —
    хеш пароля и PIN в панели не показываются), его же пишут логи."""
    secret = "secret-password-hash"
    try:
        async with engine.connect() as conn:
            with pytest.raises(DBAPIError) as exc_info:
                await conn.execute(text("SELECT CAST(:value AS text), 1 / 0"), {"value": secret})
    finally:
        await engine.dispose()  # пул не переносится между event loop тестов

    assert "division by zero" in str(exc_info.value)
    assert secret not in str(exc_info.value)
