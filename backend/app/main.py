from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.admin import mount_admin
from app.config import get_settings
from app.db import SessionFactory
from app.errors import register_error_handlers
from app.redis import redis_client
from app.routers import (
    activities,
    auth,
    bookings,
    catalog,
    chat,
    favorites,
    hosts,
    notifications,
    payments,
    support,
    users,
    vehicles,
)
from app.schemas.common import ERROR_RESPONSES

DESCRIPTION = """
API платформы «Конок». Все пути — с префиксом `/api/v1`.

**Авторизация:** `POST /auth/register` → код на email → `POST /auth/verify-email` →
`access_token` в заголовке `Authorization: Bearer <token>`.

**Ошибки:** `{"error": {"code": "...", "message": "...", "details": ...}}`.

**Списки:** `?limit=&offset=` → `{"items": [...], "total", "limit", "offset"}`.
"""


def create_app() -> FastAPI:
    app = FastAPI(title="Konok API", version="1.0", description=DESCRIPTION)
    register_error_handlers(app)
    origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"]
    )

    @app.get("/health", tags=["health"])
    async def health() -> JSONResponse:
        """ok, если доступны PostgreSQL и Redis; иначе 503."""
        try:
            async with SessionFactory() as session:
                await session.execute(text("SELECT 1"))
            await redis_client.ping()
        except Exception:
            return JSONResponse({"status": "error"}, status_code=503)
        return JSONResponse({"status": "ok"})

    api = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)
    for module in (
        auth,
        users,
        catalog,
        hosts,
        activities,
        vehicles,
        bookings,
        payments,
        favorites,
        chat,
        support,
        notifications,
    ):
        api.include_router(module.router)
    app.include_router(api)

    media = get_settings().storage_dir / "public"
    media.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=media), name="media")
    mount_admin(app, SessionFactory)
    return app


app = create_app()
