"""Управление справочником мест администратором (R-37)."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import UserRole
from tests.admin.helpers import assert_error, headers_for, place_by_slug, region_id_by_name


async def places(client: AsyncClient) -> list[dict[str, Any]]:
    response = await client.get("/places")
    assert response.status_code == 200, response.text
    data: list[dict[str, Any]] = response.json()
    return data


async def minimal_payload(client: AsyncClient, slug: str = "son-kul") -> dict[str, Any]:
    return {
        "slug": slug,
        "name": "Сон-Куль",
        "region_id": await region_id_by_name(client, "Нарынская область"),
    }


# --- создание ---


async def test_admin_creates_place_full(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-37: admin создаёт место со всеми полями → 201 PlaceOut, место видно публично."""
    _, headers = await headers_for(client, db_session)
    region_id = await region_id_by_name(client, "Чуйская область")
    payload = {
        "slug": "ala-archa",
        "name": "Ала-Арча",
        "region_id": region_id,
        "short_description": "Нацпарк рядом с Бишкеком",
        "description": "Ущелье, ледник Ак-Сай, водопад.",
        "cover_url": "https://example.kg/ala-archa.jpg",
        "stops": [{"name": "Альплагерь"}, {"name": "Водопад"}],
        "travel_options": [{"type": "taxi", "minutes": 60}],
    }

    response = await client.post("/admin/places", json=payload, headers=headers)

    assert response.status_code == 201, response.text
    data = response.json()
    for field, value in payload.items():
        assert data[field] == value, field
    assert isinstance(data["id"], int)
    assert data["created_at"]
    detail = await client.get(f"/places/{data['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json() == data


async def test_create_place_defaults(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-37 (граница): только обязательные поля → опциональные null, stops/travel_options = []."""
    _, headers = await headers_for(client, db_session)

    response = await client.post(
        "/admin/places", json=await minimal_payload(client), headers=headers
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["short_description"] is None
    assert data["description"] is None
    assert data["cover_url"] is None
    assert data["stops"] == []
    assert data["travel_options"] == []


async def test_created_place_found_by_region_filter(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-37: место привязано к одному региону — новое место попадает в фильтр своего региона."""
    _, headers = await headers_for(client, db_session)
    payload = await minimal_payload(client)
    created = await client.post("/admin/places", json=payload, headers=headers)
    assert created.status_code == 201, created.text

    response = await client.get("/places", params={"region_id": payload["region_id"]})

    assert response.status_code == 200, response.text
    assert [p["slug"] for p in response.json()] == ["tash-rabat", "son-kul"]


async def test_create_place_duplicate_slug_conflict(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-37 (нарушение): slug уникален — сидовый 'tash-rabat' → 409 conflict."""
    _, headers = await headers_for(client, db_session)

    response = await client.post(
        "/admin/places", json=await minimal_payload(client, "tash-rabat"), headers=headers
    )

    assert_error(response, 409, "conflict")
    assert len(await places(client)) == 2


async def test_create_place_unknown_region_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-37 (нарушение): место привязано к существующему региону — чужой region_id → 404."""
    _, headers = await headers_for(client, db_session)
    payload = await minimal_payload(client)
    payload["region_id"] = 999

    response = await client.post("/admin/places", json=payload, headers=headers)

    assert_error(response, 404, "not_found")
    assert len(await places(client)) == 2


@pytest.mark.parametrize("slug", ["a", "son-kul-2", "123", "a1-b2-c3"])
async def test_create_place_valid_slug_formats(
    client: AsyncClient, db_session: AsyncSession, slug: str
) -> None:
    """R-37 (граница): slug по шаблону ^[a-z0-9]+(-[a-z0-9]+)*$ принимается."""
    _, headers = await headers_for(client, db_session)

    response = await client.post(
        "/admin/places", json=await minimal_payload(client, slug), headers=headers
    )

    assert response.status_code == 201, response.text
    assert response.json()["slug"] == slug


@pytest.mark.parametrize(
    "slug",
    ["", "Son-Kul", "son_kul", "son kul", "-son", "son-", "son--kul", "сон-куль", "son.kul"],
)
async def test_create_place_invalid_slug_rejected(
    client: AsyncClient, db_session: AsyncSession, slug: str
) -> None:
    """R-37 (нарушение): slug вне шаблона ^[a-z0-9]+(-[a-z0-9]+)*$ → 422 validation_error."""
    _, headers = await headers_for(client, db_session)

    response = await client.post(
        "/admin/places", json=await minimal_payload(client, slug), headers=headers
    )

    assert_error(response, 422, "validation_error")
    assert len(await places(client)) == 2


@pytest.mark.parametrize("missing", ["slug", "name", "region_id"])
async def test_create_place_missing_required_field(
    client: AsyncClient, db_session: AsyncSession, missing: str
) -> None:
    """R-37 (граница): без обязательного поля (slug, name, region_id) → 422 validation_error."""
    _, headers = await headers_for(client, db_session)
    payload = await minimal_payload(client)
    del payload[missing]

    response = await client.post("/admin/places", json=payload, headers=headers)

    assert_error(response, 422, "validation_error")


# --- редактирование ---


async def test_admin_patches_place_partially(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-37: частичное обновление меняет только переданные поля, остальные сохраняются."""
    _, headers = await headers_for(client, db_session)
    before = await place_by_slug(client, "tash-rabat")

    response = await client.patch(
        f"/admin/places/{before['id']}",
        json={"short_description": "Караван-сарай XV века", "stops": [{"name": "Ат-Башы"}]},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["short_description"] == "Караван-сарай XV века"
    assert data["stops"] == [{"name": "Ат-Башы"}]
    for field in ("id", "slug", "name", "region_id", "description", "cover_url"):
        assert data[field] == before[field], field
    assert data["travel_options"] == before["travel_options"]
    detail = await client.get(f"/places/{before['id']}")
    assert detail.json() == data


async def test_patch_place_moves_to_other_region(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-37: место привязано к одному региону — смена region_id переносит его между фильтрами."""
    _, headers = await headers_for(client, db_session)
    place = await place_by_slug(client, "tash-rabat")
    naryn = place["region_id"]
    talas = await region_id_by_name(client, "Таласская область")

    response = await client.patch(
        f"/admin/places/{place['id']}", json={"region_id": talas}, headers=headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["region_id"] == talas
    in_naryn = await client.get("/places", params={"region_id": naryn})
    in_talas = await client.get("/places", params={"region_id": talas})
    assert in_naryn.json() == []
    assert [p["slug"] for p in in_talas.json()] == ["tash-rabat"]


async def test_patch_place_slug_taken_conflict(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-37 (нарушение): slug занят другим местом → 409 conflict, место не меняется."""
    _, headers = await headers_for(client, db_session)
    place = await place_by_slug(client, "tash-rabat")

    response = await client.patch(
        f"/admin/places/{place['id']}", json={"slug": "issyk-kul"}, headers=headers
    )

    assert_error(response, 409, "conflict")
    assert (await client.get(f"/places/{place['id']}")).json() == place


async def test_patch_place_same_slug_ok(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-37 (граница): передать собственный slug не конфликт."""
    _, headers = await headers_for(client, db_session)
    place = await place_by_slug(client, "tash-rabat")

    response = await client.patch(
        f"/admin/places/{place['id']}",
        json={"slug": "tash-rabat", "name": "Таш-Рабат (караван-сарай)"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["slug"] == "tash-rabat"
    assert response.json()["name"] == "Таш-Рабат (караван-сарай)"


async def test_patch_place_unknown_region_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-37 (нарушение): несуществующий region_id → 404 not_found, регион не меняется."""
    _, headers = await headers_for(client, db_session)
    place = await place_by_slug(client, "tash-rabat")

    response = await client.patch(
        f"/admin/places/{place['id']}", json={"region_id": 999}, headers=headers
    )

    assert_error(response, 404, "not_found")
    assert (await client.get(f"/places/{place['id']}")).json() == place


async def test_patch_place_invalid_slug_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-37 (нарушение): невалидный slug при обновлении → 422 validation_error."""
    _, headers = await headers_for(client, db_session)
    place = await place_by_slug(client, "tash-rabat")

    response = await client.patch(
        f"/admin/places/{place['id']}", json={"slug": "Tash Rabat"}, headers=headers
    )

    assert_error(response, 422, "validation_error")
    assert (await client.get(f"/places/{place['id']}")).json() == place


async def test_patch_unknown_place_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-37: несуществующее место → 404 not_found."""
    _, headers = await headers_for(client, db_session)

    response = await client.patch("/admin/places/999999", json={"name": "X"}, headers=headers)

    assert_error(response, 404, "not_found")


# --- удаление ---


async def test_admin_deletes_place(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-37: admin удаляет место → 204, GET /places/{id} → 404, второе место на месте."""
    _, headers = await headers_for(client, db_session)
    place = await place_by_slug(client, "tash-rabat")

    response = await client.delete(f"/admin/places/{place['id']}", headers=headers)

    assert response.status_code == 204, response.text
    assert_error(await client.get(f"/places/{place['id']}"), 404, "not_found")
    assert [p["slug"] for p in await places(client)] == ["issyk-kul"]


async def test_delete_place_twice_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-37 (граница): повторное удаление → 404 not_found."""
    _, headers = await headers_for(client, db_session)
    place = await place_by_slug(client, "tash-rabat")
    first = await client.delete(f"/admin/places/{place['id']}", headers=headers)
    assert first.status_code == 204, first.text

    response = await client.delete(f"/admin/places/{place['id']}", headers=headers)

    assert_error(response, 404, "not_found")


# --- доступ ---

PLACE_CALLS = [
    ("POST", "/admin/places", {"slug": "son-kul", "name": "Сон-Куль", "region_id": 1}),
    ("PATCH", "/admin/places/{pid}", {"name": "Чужое имя"}),
    ("DELETE", "/admin/places/{pid}", None),
]


@pytest.mark.parametrize(("method", "path", "body"), PLACE_CALLS)
@pytest.mark.parametrize("role", [UserRole.USER, UserRole.MODERATOR])
async def test_non_admin_cannot_manage_places(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    """R-37: местами управляет только admin — user и moderator → 403, справочник не меняется."""
    _, headers = await headers_for(client, db_session, role)
    before = await places(client)
    url = path.format(pid=(await place_by_slug(client, "tash-rabat"))["id"])

    response = await client.request(method, url, json=body, headers=headers)

    assert_error(response, 403, "permission_denied")
    assert await places(client) == before


@pytest.mark.parametrize(("method", "path", "body"), PLACE_CALLS)
async def test_manage_places_requires_auth(
    client: AsyncClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    """R-37: без токена → 401 unauthorized, справочник не меняется."""
    before = await places(client)
    url = path.format(pid=(await place_by_slug(client, "tash-rabat"))["id"])

    response = await client.request(method, url, json=body)

    assert_error(response, 401, "unauthorized")
    assert await places(client) == before
