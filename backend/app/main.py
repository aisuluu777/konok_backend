from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.db import engine
from app.core.errors import register_error_handlers
from app.core.redis import redis_client
from app.modules.activities.router import router as activities_router
from app.modules.admin.router import admin_router, public_router
from app.modules.bookings.router import router as bookings_router
from app.modules.chat.router import router as chat_router
from app.modules.favorites.router import router as favorites_router
from app.modules.files.router import router as files_router
from app.modules.guests.router import router as guests_router
from app.modules.hosts.router import router as hosts_router
from app.modules.kyc.router import router as kyc_router
from app.modules.notifications.router import router as notifications_router
from app.modules.payments.router import router as payments_router
from app.modules.reviews.router import router as reviews_router
from app.modules.support.router import router as support_router
from app.modules.users.router import admin_users_router, auth_router, users_router
from app.modules.vehicles.router import router as vehicles_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await redis_client.aclose()
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Konok API", lifespan=lifespan)
    register_error_handlers(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # Роутеры модулей подключаются здесь по мере реализации шагов.
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(admin_users_router)
    app.include_router(public_router)
    app.include_router(admin_router)
    app.include_router(notifications_router)
    app.include_router(files_router)
    app.include_router(kyc_router)
    app.include_router(guests_router)
    app.include_router(hosts_router)
    app.include_router(activities_router)
    app.include_router(vehicles_router)
    app.include_router(bookings_router)
    app.include_router(payments_router)
    app.include_router(reviews_router)
    app.include_router(chat_router)
    app.include_router(support_router)
    app.include_router(favorites_router)
    return app


app = create_app()
