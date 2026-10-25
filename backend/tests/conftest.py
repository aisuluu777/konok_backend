import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal

# Тесты работают с отдельной БД; переменные задаются до импорта приложения.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://konok:konok@localhost:5433/konok_test"
)
os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef0123456789")
os.environ["PAYMENT_PROVIDER"] = "mock"
os.environ["AZURE_TRANSLATOR_KEY"] = ""

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.db import SessionFactory  # noqa: E402
from app.main import app  # noqa: E402
from app.models import HostProfile, Locality, User, Vehicle  # noqa: E402
from app.rules import today_bishkek  # noqa: E402
from app.security import create_access_token, generate_code, hash_password  # noqa: E402

# Справочники и настройки из миграции не трогаем, остальное чистим перед каждым тестом.
_KEEP = {"regions", "localities", "settings", "alembic_version"}


@pytest.fixture(scope="session", autouse=True)
def migrate() -> None:
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)


@pytest.fixture(autouse=True)
async def clean_db() -> None:
    async with SessionFactory() as session:
        tables = await session.scalars(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        names = ", ".join(t for t in tables if t not in _KEEP)
        await session.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        await session.commit()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def locality_id() -> int:
    async with SessionFactory() as session:
        return (await session.scalar(select(Locality.id).limit(1))) or 0


async def make_user(*, verified: bool = True, license: bool = False, name: str = "user") -> User:
    async with SessionFactory() as session:
        user = User(
            email=f"{name}-{generate_code()}@test.kg",
            password_hash=hash_password("password123"),
            full_name=name,
            is_email_confirmed=True,
            is_verified=verified,
            is_driver_license_verified=license,
            checkin_pin=generate_code(),
        )
        session.add(user)
        await session.commit()
        return user


def auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


async def make_host(capacity: int = 4, price: str = "1000.00") -> User:
    user = await make_user(name="host")
    async with SessionFactory() as session:
        session.add(
            HostProfile(
                user_id=user.id,
                locality_id=await locality_id(),
                address="ул. Тестовая, 1",
                capacity=capacity,
                price_per_person=Decimal(price),
            )
        )
        await session.commit()
    return user


async def make_vehicle(price: str = "3000.00") -> tuple[User, int]:
    owner = await make_user(name="owner")
    async with SessionFactory() as session:
        vehicle = Vehicle(
            owner_id=owner.id,
            locality_id=await locality_id(),
            title="Toyota RAV4",
            price_per_day=Decimal(price),
        )
        session.add(vehicle)
        await session.commit()
        return owner, vehicle.id


def day(offset: int) -> str:
    return (today_bishkek() + timedelta(days=offset)).isoformat()


async def book_stay(
    client: AsyncClient, guest: User, host: User, start: int, end: int, guests: int = 1
) -> dict:
    """Создаёт бронь проживания; возвращает ответ PaymentStart (или ошибку)."""
    response = await client.post(
        "/api/v1/bookings/stay",
        headers=auth(guest),
        json={
            "host_id": host.id,
            "start_date": day(start),
            "end_date": day(end),
            "guests_count": guests,
        },
    )
    return {"status": response.status_code, **response.json()}


async def pay(client: AsyncClient, guest: User, payment_id: int) -> int:
    response = await client.post(f"/api/v1/payments/{payment_id}/mock-pay", headers=auth(guest))
    return response.status_code


async def confirmed_stay(
    client: AsyncClient, guest: User, host: User, start: int, end: int, guests: int = 1
) -> dict:
    created = await book_stay(client, guest, host, start, end, guests)
    assert created["status"] == 201, created
    assert await pay(client, guest, created["payment"]["id"]) == 200
    return created
