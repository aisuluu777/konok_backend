"""Общие хелперы тестов админки справочников и настроек."""

from typing import Any

from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import User, UserRole
from tests.factories import auth, create_user, login


def assert_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert "error" in body, body
    assert body["error"]["code"] == code, body


async def headers_for(
    client: AsyncClient, db_session: AsyncSession, role: UserRole = UserRole.ADMIN
) -> tuple[User, dict[str, str]]:
    user = await create_user(db_session, role=role)
    tokens = await login(client, user.email)
    return user, auth(tokens["access_token"])


async def region_id_by_name(client: AsyncClient, name: str) -> int:
    response = await client.get("/regions")
    assert response.status_code == 200, response.text
    ids = [r["id"] for r in response.json() if r["name"] == name]
    assert ids, f"регион {name!r} не найден в GET /regions: {response.text}"
    return int(ids[0])


async def place_by_slug(client: AsyncClient, slug: str) -> dict[str, Any]:
    response = await client.get("/places")
    assert response.status_code == 200, response.text
    found = [p for p in response.json() if p["slug"] == slug]
    assert found, f"место {slug!r} не найдено в GET /places: {response.text}"
    place: dict[str, Any] = found[0]
    return place
