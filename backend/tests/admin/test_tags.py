"""Управление тегами администратором (R-09)."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.admin.models import Tag
from app.modules.users.models import UserRole
from tests.admin.helpers import assert_error, headers_for


async def tags(client: AsyncClient) -> list[dict[str, Any]]:
    response = await client.get("/tags")
    assert response.status_code == 200, response.text
    data: list[dict[str, Any]] = response.json()
    return data


async def tag_id(client: AsyncClient, name: str) -> int:
    ids = [t["id"] for t in await tags(client) if t["name"] == name]
    assert ids, f"тег {name!r} не найден"
    return int(ids[0])


async def tag_count(db_session: AsyncSession) -> int:
    count = await db_session.scalar(select(func.count()).select_from(Tag))
    return int(count or 0)


# --- создание ---


async def test_admin_creates_tag(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-09: admin создаёт тег → 201 {id, name}, тег виден в публичном GET /tags."""
    _, headers = await headers_for(client, db_session)

    response = await client.post("/admin/tags", json={"name": "Охота с беркутом"}, headers=headers)

    assert response.status_code == 201, response.text
    data = response.json()
    assert set(data) == {"id", "name"}
    assert data["name"] == "Охота с беркутом"
    assert {"id": data["id"], "name": "Охота с беркутом"} in await tags(client)


async def test_create_tag_duplicate_name_conflict(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-09 (нарушение): имя тега уникально — дубликат сидового «Готовка» → 409 conflict."""
    _, headers = await headers_for(client, db_session)
    before = await tag_count(db_session)

    response = await client.post("/admin/tags", json={"name": "Готовка"}, headers=headers)

    assert_error(response, 409, "conflict")
    assert await tag_count(db_session) == before


@pytest.mark.parametrize("name", ["", "   "])
async def test_create_tag_blank_name_rejected(
    client: AsyncClient, db_session: AsyncSession, name: str
) -> None:
    """R-09 (граница): пустое имя или одни пробелы → 422 validation_error, тег не создан."""
    _, headers = await headers_for(client, db_session)
    before = await tag_count(db_session)

    response = await client.post("/admin/tags", json={"name": name}, headers=headers)

    assert_error(response, 422, "validation_error")
    assert await tag_count(db_session) == before


async def test_create_tag_missing_name_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-09 (граница): тело без name → 422 validation_error."""
    _, headers = await headers_for(client, db_session)

    response = await client.post("/admin/tags", json={}, headers=headers)

    assert_error(response, 422, "validation_error")


# --- переименование ---


async def test_admin_renames_tag(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-09: admin переименовывает тег → 200, новое имя в GET /tags, старого нет."""
    _, headers = await headers_for(client, db_session)
    tid = await tag_id(client, "Рыбалка")

    response = await client.patch(
        f"/admin/tags/{tid}", json={"name": "Рыбалка на озере"}, headers=headers
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"id": tid, "name": "Рыбалка на озере"}
    names = {t["name"] for t in await tags(client)}
    assert "Рыбалка на озере" in names
    assert "Рыбалка" not in names


async def test_rename_tag_to_taken_name_conflict(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-09 (нарушение): имя занято другим тегом → 409 conflict, имя не меняется."""
    _, headers = await headers_for(client, db_session)
    tid = await tag_id(client, "Рыбалка")

    response = await client.patch(f"/admin/tags/{tid}", json={"name": "Походы"}, headers=headers)

    assert_error(response, 409, "conflict")
    assert {"id": tid, "name": "Рыбалка"} in await tags(client)


async def test_rename_tag_to_own_name_ok(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-09 (граница): переименование в своё же имя не конфликт — занято «другим тегом» не им."""
    _, headers = await headers_for(client, db_session)
    tid = await tag_id(client, "Рыбалка")

    response = await client.patch(f"/admin/tags/{tid}", json={"name": "Рыбалка"}, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"id": tid, "name": "Рыбалка"}


async def test_rename_tag_blank_name_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-09 (граница): пустое имя при переименовании → 422 validation_error."""
    _, headers = await headers_for(client, db_session)
    tid = await tag_id(client, "Рыбалка")

    response = await client.patch(f"/admin/tags/{tid}", json={"name": "  "}, headers=headers)

    assert_error(response, 422, "validation_error")
    assert {"id": tid, "name": "Рыбалка"} in await tags(client)


async def test_rename_unknown_tag_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-09: несуществующий тег → 404 not_found."""
    _, headers = await headers_for(client, db_session)

    response = await client.patch("/admin/tags/9999", json={"name": "Новое"}, headers=headers)

    assert_error(response, 404, "not_found")


# --- удаление ---


async def test_admin_deletes_tag(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-09: admin удаляет тег → 204, тега нет в GET /tags, остальные на месте."""
    _, headers = await headers_for(client, db_session)
    tid = await tag_id(client, "Фототуры")

    response = await client.delete(f"/admin/tags/{tid}", headers=headers)

    assert response.status_code == 204, response.text
    remaining = await tags(client)
    assert tid not in {t["id"] for t in remaining}
    assert len(remaining) == 8


async def test_delete_tag_twice_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-09 (граница): повторное удаление того же тега → 404 not_found."""
    _, headers = await headers_for(client, db_session)
    tid = await tag_id(client, "Фототуры")
    first = await client.delete(f"/admin/tags/{tid}", headers=headers)
    assert first.status_code == 204, first.text

    response = await client.delete(f"/admin/tags/{tid}", headers=headers)

    assert_error(response, 404, "not_found")


async def test_deleted_tag_name_can_be_reused(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-09 (граница): после удаления имя свободно — создание тега с тем же именем → 201."""
    _, headers = await headers_for(client, db_session)
    tid = await tag_id(client, "Фототуры")
    deleted = await client.delete(f"/admin/tags/{tid}", headers=headers)
    assert deleted.status_code == 204, deleted.text

    response = await client.post("/admin/tags", json={"name": "Фототуры"}, headers=headers)

    assert response.status_code == 201, response.text


# --- доступ ---

TAG_CALLS = [
    ("POST", "/admin/tags", {"name": "Чужой тег"}),
    ("PATCH", "/admin/tags/{tid}", {"name": "Чужой тег"}),
    ("DELETE", "/admin/tags/{tid}", None),
]


@pytest.mark.parametrize(("method", "path", "body"), TAG_CALLS)
@pytest.mark.parametrize("role", [UserRole.USER, UserRole.MODERATOR])
async def test_non_admin_cannot_manage_tags(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    method: str,
    path: str,
    body: dict[str, str] | None,
) -> None:
    """R-09: теги ведёт только администратор — user и moderator → 403, список не меняется."""
    _, headers = await headers_for(client, db_session, role)
    before = await tags(client)
    url = path.format(tid=await tag_id(client, "Рыбалка"))

    response = await client.request(method, url, json=body, headers=headers)

    assert_error(response, 403, "permission_denied")
    assert await tags(client) == before


@pytest.mark.parametrize(("method", "path", "body"), TAG_CALLS)
async def test_manage_tags_requires_auth(
    client: AsyncClient, method: str, path: str, body: dict[str, str] | None
) -> None:
    """R-09: без токена → 401 unauthorized, список не меняется."""
    before = await tags(client)
    url = path.format(tid=await tag_id(client, "Рыбалка"))

    response = await client.request(method, url, json=body)

    assert_error(response, 401, "unauthorized")
    assert await tags(client) == before
