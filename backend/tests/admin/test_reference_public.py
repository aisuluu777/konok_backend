"""Публичное чтение справочников: регионы, теги, места (R-09, R-37)."""

from httpx import AsyncClient

from tests.admin.helpers import assert_error, place_by_slug, region_id_by_name

SEEDED_REGIONS = {
    "г. Бишкек",
    "г. Ош",
    "Чуйская область",
    "Иссык-Кульская область",
    "Нарынская область",
    "Таласская область",
    "Джалал-Абадская область",
    "Ошская область",
    "Баткенская область",
}
SEEDED_TAGS = {
    "Верховая езда",
    "Готовка",
    "Ремёсла",
    "Жизнь в юрте",
    "Походы",
    "Рыбалка",
    "Культура",
    "Фототуры",
    "Природа",
}
PLACE_FIELDS = {
    "id",
    "slug",
    "name",
    "region_id",
    "short_description",
    "description",
    "cover_url",
    "stops",
    "travel_options",
    "created_at",
}


async def test_regions_public_list_sorted(client: AsyncClient) -> None:
    """R-37 (место привязано к региону): GET /regions без авторизации → 9 регионов из сида по id."""
    response = await client.get("/regions")

    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data) == 9
    assert all(set(r) == {"id", "name"} for r in data), data
    assert {r["name"] for r in data} == SEEDED_REGIONS
    ids = [r["id"] for r in data]
    assert ids == sorted(ids)


async def test_tags_public_list_sorted(client: AsyncClient) -> None:
    """R-09: список тегов читают все — GET /tags без авторизации → 9 тегов из сида по id."""
    response = await client.get("/tags")

    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data) == 9
    assert all(set(t) == {"id", "name"} for t in data), data
    assert {t["name"] for t in data} == SEEDED_TAGS
    ids = [t["id"] for t in data]
    assert ids == sorted(ids)


async def test_places_public_list(client: AsyncClient) -> None:
    """R-37: чтение мест публичное — GET /places без авторизации → оба места из сида по id."""
    response = await client.get("/places")

    assert response.status_code == 200, response.text
    data = response.json()
    assert {p["slug"] for p in data} == {"tash-rabat", "issyk-kul"}
    assert all(set(p) >= PLACE_FIELDS for p in data), data
    ids = [p["id"] for p in data]
    assert ids == sorted(ids)
    tash = next(p for p in data if p["slug"] == "tash-rabat")
    assert tash["region_id"] == await region_id_by_name(client, "Нарынская область")
    assert tash["stops"] == []
    assert tash["travel_options"] == []


async def test_places_filter_by_region(client: AsyncClient) -> None:
    """R-37: место привязано к одному региону — фильтр region_id отдаёт только места региона."""
    naryn = await region_id_by_name(client, "Нарынская область")

    response = await client.get("/places", params={"region_id": naryn})

    assert response.status_code == 200, response.text
    assert [p["slug"] for p in response.json()] == ["tash-rabat"]


async def test_places_filter_by_region_without_places(client: AsyncClient) -> None:
    """R-37 (граница): регион без мест → пустой список, не ошибка."""
    batken = await region_id_by_name(client, "Баткенская область")

    response = await client.get("/places", params={"region_id": batken})

    assert response.status_code == 200, response.text
    assert response.json() == []


async def test_place_detail_public(client: AsyncClient) -> None:
    """R-37: GET /places/{id} без авторизации → 200 PlaceOut."""
    place = await place_by_slug(client, "issyk-kul")

    response = await client.get(f"/places/{place['id']}")

    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data) >= PLACE_FIELDS
    assert data["slug"] == "issyk-kul"
    assert data["name"] == "Иссык-Куль"
    assert data["region_id"] == await region_id_by_name(client, "Иссык-Кульская область")


async def test_place_detail_not_found(client: AsyncClient) -> None:
    """R-37: несуществующее место → 404 not_found."""
    response = await client.get("/places/999999")

    assert_error(response, 404, "not_found")
