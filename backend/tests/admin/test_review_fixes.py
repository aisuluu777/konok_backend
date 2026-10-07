"""Граничный ввод справочников и настроек (ревью Step 2)."""

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.modules.users.models import UserRole
from tests.factories import auth, create_user, login


@pytest.mark.parametrize(
    "url", ["/places?region_id=40000", "/places/9223372036854775808", "/places/0"]
)
async def test_out_of_range_ids_are_422_not_500(client: AsyncClient, url: str) -> None:
    """R-37: публичное чтение не падает на id вне диапазона колонки."""
    response = await client.get(url)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("access_token_ttl_min", "abc"),
        ("access_token_ttl_min", "0"),
        ("otp_ttl_sec", "-5"),
        ("checkin_time", "25:00"),
        ("commission_rate", "1.5"),
        ("commission_rate", "x"),
    ],
)
async def test_invalid_setting_value_rejected(
    client: AsyncClient, db_session: AsyncSession, key: str, value: str
) -> None:
    """R-28: неверное значение не сохраняется и не пишется в журнал."""
    admin = await create_user(db_session, role=UserRole.ADMIN)
    headers = auth((await login(client, admin.email))["access_token"])
    before = (await client.get("/admin/settings", headers=headers)).json()

    response = await client.put(f"/admin/settings/{key}", headers=headers, json={"value": value})

    assert response.status_code == 400
    assert (await client.get("/admin/settings", headers=headers)).json() == before
    assert await db_session.scalar(select(func.count()).select_from(AuditLog)) == 0


@pytest.mark.parametrize(
    ("key", "value"),
    [("checkin_time", "14:00"), ("commission_rate", "0.1200"), ("hold_minutes", "20")],
)
async def test_valid_setting_value_accepted(
    client: AsyncClient, db_session: AsyncSession, key: str, value: str
) -> None:
    """R-28: корректные значения разных форматов сохраняются."""
    admin = await create_user(db_session, role=UserRole.ADMIN)
    headers = auth((await login(client, admin.email))["access_token"])

    response = await client.put(f"/admin/settings/{key}", headers=headers, json={"value": value})

    assert response.status_code == 200
    assert response.json()["value"] == value


@pytest.mark.parametrize("field", ["slug", "name", "region_id", "stops", "travel_options"])
async def test_patch_place_null_in_required_field_keeps_value(
    client: AsyncClient, db_session: AsyncSession, field: str
) -> None:
    """R-37: null в обязательном поле PATCH = «не менять»."""
    admin = await create_user(db_session, role=UserRole.ADMIN)
    headers = auth((await login(client, admin.email))["access_token"])
    place = (await client.get("/places")).json()[0]

    response = await client.patch(
        f"/admin/places/{place['id']}", headers=headers, json={field: None}
    )

    assert response.status_code == 200
    assert response.json()[field] == place[field]
