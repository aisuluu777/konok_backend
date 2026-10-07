"""commission_rate в settings: формат как у bookings.commission_rate — NUMERIC(5,4), 0 <= x < 1.

Иначе каждая новая бронь падала бы на CHECK (500) или ставка молча округлялась (R-33).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import UserRole
from tests.factories import auth, create_user, login


async def put_rate(client: AsyncClient, db_session: AsyncSession, value: str) -> int:
    admin = await create_user(db_session, role=UserRole.ADMIN)
    headers = auth((await login(client, admin.email))["access_token"])
    response = await client.put(
        "/admin/settings/commission_rate", headers=headers, json={"value": value}
    )
    return response.status_code


@pytest.mark.parametrize("value", ["1", "1.0000", "-0.0001", "0.12345"])
async def test_rate_out_of_booking_range_rejected(
    client: AsyncClient, db_session: AsyncSession, value: str
) -> None:
    """R-33: ставка 1 и больше, отрицательная или с 5+ знаками → 400."""
    assert await put_rate(client, db_session, value) == 400


@pytest.mark.parametrize("value", ["0", "0.9999", "0.25"])
async def test_rate_in_range_accepted(
    client: AsyncClient, db_session: AsyncSession, value: str
) -> None:
    """R-33: 0 <= ставка < 1 с точностью до 4 знаков сохраняется."""
    assert await put_rate(client, db_session, value) == 200
