"""Машины: POST/PATCH /vehicles, GET /vehicles/my, каталог GET /vehicles и GET /vehicles/{id}.

R-02 — машину добавляет любой user и становится владельцем; R-17 — цена за день > 0;
R-03 — в каталоге только опубликованная машина владельца с одобренным KYC, заблокированный
владелец скрыт; R-40 — владелец сам включает/снимает публикацию, удаления нет; R-36 — сом.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import KycStatus, User, UserStatus
from tests.admin.helpers import assert_error, region_id_by_name
from tests.hosts.test_hosts import JPEG, first_region_id, set_kyc, upload, user_with_headers

OWNER_FIELDS = {
    "id",
    "owner_id",
    "region_id",
    "model",
    "year",
    "seats",
    "transmission",
    "fuel",
    "specs",
    "included",
    "photos",
    "price_per_day",
    "with_driver",
    "rating_avg",
    "reviews_count",
    "is_published",
    "created_at",
    "updated_at",
}
PUBLIC_FIELDS = OWNER_FIELDS - {"is_published", "created_at", "updated_at"}


async def owner_with_headers(
    db_session: AsyncSession, client: AsyncClient, *, kyc: KycStatus = KycStatus.APPROVED
) -> tuple[User, dict[str, str]]:
    user, headers = await user_with_headers(db_session, client)
    await set_kyc(db_session, user.id, kyc)
    return user, headers


def vehicle_body(region_id: int, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "region_id": region_id,
        "model": "Toyota Land Cruiser",
        "price_per_day": "5000.00",
    }
    body.update(overrides)
    return body


async def create_vehicle(
    client: AsyncClient, headers: dict[str, str], body: dict[str, Any]
) -> dict[str, Any]:
    response = await client.post("/vehicles", headers=headers, json=body)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


async def vehicle_rows(db_session: AsyncSession) -> int:
    count: int = await db_session.scalar(text("SELECT count(*) FROM vehicles")) or 0
    return count


async def catalog(client: AsyncClient, **params: Any) -> list[int]:
    response = await client.get("/vehicles", params=params)
    assert response.status_code == 200, response.text
    return [int(v["id"]) for v in response.json()]


# --- без токена ---


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/vehicles"), ("GET", "/vehicles/my"), ("PATCH", "/vehicles/1")],
)
async def test_vehicles_require_auth(client: AsyncClient, method: str, path: str) -> None:
    """Управление машинами без токена → 401."""
    response = await client.request(method, path, json={})
    assert_error(response, 401, "unauthorized")


# --- создание ---


async def test_create_minimal_vehicle(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-02, R-17, R-40: любой user добавляет машину, по умолчанию она опубликована."""
    user, headers = await user_with_headers(db_session, client)
    region_id = await first_region_id(client)

    data = await create_vehicle(client, headers, vehicle_body(region_id))

    assert set(data) == OWNER_FIELDS
    assert data["owner_id"] == user.id
    assert data["region_id"] == region_id
    assert data["model"] == "Toyota Land Cruiser"
    assert data["price_per_day"] == "5000.00"
    assert data["is_published"] is True
    assert data["with_driver"] is False
    assert data["specs"] == {}
    assert data["included"] == []
    assert data["photos"] == []
    assert data["year"] is None
    assert data["rating_avg"] == "0.00"
    assert data["reviews_count"] == 0


async def test_create_full_vehicle(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-17: все поля сохраняются, порядок фото сохраняется."""
    _, headers = await user_with_headers(db_session, client)
    region_id = await first_region_id(client)
    photos = [await upload(client, headers, JPEG), await upload(client, headers, JPEG)]
    body = vehicle_body(
        region_id,
        year=2018,
        seats=7,
        transmission="автомат",
        fuel="дизель",
        specs={"drive": "4x4"},
        included=["страховка", "детское кресло"],
        photos=photos,
        with_driver=True,
        is_published=False,
    )

    data = await create_vehicle(client, headers, body)

    for key in ("year", "seats", "transmission", "fuel", "specs", "included", "photos"):
        assert data[key] == body[key]
    assert data["with_driver"] is True
    assert data["is_published"] is False


@pytest.mark.parametrize("price", ["0", "0.00", "-1"])
async def test_price_must_be_positive(
    client: AsyncClient, db_session: AsyncSession, price: str
) -> None:
    """R-17: цена за день > 0."""
    _, headers = await user_with_headers(db_session, client)
    body = vehicle_body(await first_region_id(client), price_per_day=price)
    assert_error(
        await client.post("/vehicles", headers=headers, json=body), 422, "validation_error"
    )
    assert await vehicle_rows(db_session) == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"model": ""},
        {"model": None},
        {"price_per_day": None},
        {"price_per_day": "1.001"},
        {"region_id": None},
        {"seats": 0},
        {"year": 1800},
        {"photos": ["https://evil.example/x.jpg"]},
        {"specs": []},
        {"specs": {"drive": {"nested": "x"}}},
        {"specs": {"drive": "x" * 201}},
        {"specs": {f"k{i}": "v" for i in range(31)}},
        {"included": [{"x": 1}]},
        {"included": ["x" * 201]},
        {"included": ["x"] * 31},
    ],
)
async def test_create_invalid_body(
    client: AsyncClient, db_session: AsyncSession, overrides: dict[str, Any]
) -> None:
    """Неверное тело → 422, машина не создаётся."""
    _, headers = await user_with_headers(db_session, client)
    body = vehicle_body(await first_region_id(client))
    body.update(overrides)
    assert_error(
        await client.post("/vehicles", headers=headers, json=body), 422, "validation_error"
    )
    assert await vehicle_rows(db_session) == 0


async def test_create_unknown_region(client: AsyncClient, db_session: AsyncSession) -> None:
    """Несуществующий регион → 404."""
    _, headers = await user_with_headers(db_session, client)
    response = await client.post("/vehicles", headers=headers, json=vehicle_body(999))
    assert_error(response, 404, "not_found")
    assert await vehicle_rows(db_session) == 0


# --- изменение ---


async def test_update_vehicle(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-17, R-40: владелец меняет цену, регион и снимает публикацию."""
    _, headers = await user_with_headers(db_session, client)
    created = await create_vehicle(client, headers, vehicle_body(await first_region_id(client)))
    other_region = await region_id_by_name(client, "Нарынская область")

    response = await client.patch(
        f"/vehicles/{created['id']}",
        headers=headers,
        json={
            "price_per_day": "4500.50",
            "region_id": other_region,
            "is_published": False,
            "specs": {"drive": "4x4"},
            "included": ["страховка"],
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["price_per_day"] == "4500.50"
    assert data["region_id"] == other_region
    assert data["is_published"] is False
    assert data["specs"] == {"drive": "4x4"}
    assert data["included"] == ["страховка"]
    assert data["model"] == created["model"]
    assert data["updated_at"] > created["updated_at"]


async def test_update_nulls(client: AsyncClient, db_session: AsyncSession) -> None:
    """null в year/seats/transmission/fuel очищает поле, в обязательных — не меняет."""
    _, headers = await user_with_headers(db_session, client)
    photos = [await upload(client, headers, JPEG)]
    body = vehicle_body(
        await first_region_id(client), year=2018, seats=5, fuel="бензин", photos=photos
    )
    created = await create_vehicle(client, headers, body)

    response = await client.patch(
        f"/vehicles/{created['id']}",
        headers=headers,
        json={"year": None, "fuel": None, "model": None, "price_per_day": None, "photos": None},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["year"] is None
    assert data["fuel"] is None
    assert data["seats"] == 5
    assert data["model"] == created["model"]
    assert data["price_per_day"] == created["price_per_day"]
    assert data["photos"] == photos


async def test_update_invalid(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-17: цена <= 0 при изменении → 422; неизвестный регион → 404."""
    _, headers = await user_with_headers(db_session, client)
    created = await create_vehicle(client, headers, vehicle_body(await first_region_id(client)))
    url = f"/vehicles/{created['id']}"

    response = await client.patch(url, headers=headers, json={"price_per_day": "0"})
    assert_error(response, 422, "validation_error")
    assert_error(
        await client.patch(url, headers=headers, json={"region_id": 999}), 404, "not_found"
    )


async def test_only_owner_updates(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-02: чужую машину менять нельзя — 404, как несуществующую."""
    _, owner = await user_with_headers(db_session, client)
    _, other = await user_with_headers(db_session, client)
    created = await create_vehicle(client, owner, vehicle_body(await first_region_id(client)))

    response = await client.patch(
        f"/vehicles/{created['id']}", headers=other, json={"price_per_day": "1"}
    )
    assert_error(response, 404, "not_found")
    assert_error(await client.patch("/vehicles/999999", headers=owner, json={}), 404, "not_found")

    mine = (await client.get("/vehicles/my", headers=owner)).json()
    assert mine[0]["price_per_day"] == created["price_per_day"]


async def test_no_delete_endpoint(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-40: машина не удаляется."""
    _, headers = await user_with_headers(db_session, client)
    created = await create_vehicle(client, headers, vehicle_body(await first_region_id(client)))
    response = await client.delete(f"/vehicles/{created['id']}", headers=headers)
    assert response.status_code == 405
    assert await vehicle_rows(db_session) == 1


async def test_my_vehicles(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-40: в своём списке владелец видит и неопубликованные машины; чужих там нет."""
    _, headers = await user_with_headers(db_session, client)
    _, other = await user_with_headers(db_session, client)
    region_id = await first_region_id(client)
    a = await create_vehicle(client, headers, vehicle_body(region_id, is_published=False))
    b = await create_vehicle(client, headers, vehicle_body(region_id))
    await create_vehicle(client, other, vehicle_body(region_id))

    response = await client.get("/vehicles/my", headers=headers)

    assert response.status_code == 200, response.text
    data = response.json()
    assert [(v["id"], v["is_published"]) for v in data] == [(a["id"], False), (b["id"], True)]
    assert set(data[0]) == OWNER_FIELDS


# --- каталог (R-03, R-40) ---


async def test_catalog_shape(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-03: опубликованная машина владельца с одобренным KYC видна публично."""
    _, headers = await owner_with_headers(db_session, client)
    created = await create_vehicle(client, headers, vehicle_body(await first_region_id(client)))

    response = await client.get("/vehicles")
    assert response.status_code == 200, response.text
    data = response.json()
    assert [v["id"] for v in data] == [created["id"]]
    assert set(data[0]) == PUBLIC_FIELDS

    response = await client.get(f"/vehicles/{created['id']}")
    assert response.status_code == 200, response.text
    assert set(response.json()) == PUBLIC_FIELDS


async def test_catalog_only_published(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-40: снятая с публикации машина не видна; вернули публикацию — снова видна."""
    _, headers = await owner_with_headers(db_session, client)
    region_id = await first_region_id(client)
    shown = await create_vehicle(client, headers, vehicle_body(region_id))
    hidden = await create_vehicle(client, headers, vehicle_body(region_id, is_published=False))

    assert await catalog(client) == [shown["id"]]
    assert_error(await client.get(f"/vehicles/{hidden['id']}"), 404, "not_found")

    response = await client.patch(
        f"/vehicles/{hidden['id']}", headers=headers, json={"is_published": True}
    )
    assert response.status_code == 200, response.text
    assert await catalog(client) == [shown["id"], hidden["id"]]


@pytest.mark.parametrize(
    "kyc",
    [KycStatus.NOT_STARTED, KycStatus.PENDING, KycStatus.REJECTED, KycStatus.RESUBMISSION],
)
async def test_catalog_only_approved_owner(
    client: AsyncClient, db_session: AsyncSession, kyc: KycStatus
) -> None:
    """R-03: машина владельца без одобренного KYC публично не видна."""
    _, approved = await owner_with_headers(db_session, client)
    _, unapproved = await owner_with_headers(db_session, client, kyc=kyc)
    region_id = await first_region_id(client)
    shown = await create_vehicle(client, approved, vehicle_body(region_id))
    hidden = await create_vehicle(client, unapproved, vehicle_body(region_id))

    assert await catalog(client) == [shown["id"]]
    assert_error(await client.get(f"/vehicles/{hidden['id']}"), 404, "not_found")


async def test_catalog_hides_blocked_owner(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-03: машина заблокированного владельца публично не видна."""
    _, active = await owner_with_headers(db_session, client)
    blocked, headers = await owner_with_headers(db_session, client)
    region_id = await first_region_id(client)
    shown = await create_vehicle(client, active, vehicle_body(region_id))
    hidden = await create_vehicle(client, headers, vehicle_body(region_id))
    await db_session.execute(
        update(User).where(User.id == blocked.id).values(status=UserStatus.BLOCKED)
    )
    await db_session.commit()

    assert await catalog(client) == [shown["id"]]
    assert_error(await client.get(f"/vehicles/{hidden['id']}"), 404, "not_found")


async def test_catalog_region_filter(client: AsyncClient, db_session: AsyncSession) -> None:
    """Фильтр каталога по региону."""
    _, headers = await owner_with_headers(db_session, client)
    naryn = await region_id_by_name(client, "Нарынская область")
    issyk_kul = await region_id_by_name(client, "Иссык-Кульская область")
    a = await create_vehicle(client, headers, vehicle_body(naryn))
    b = await create_vehicle(client, headers, vehicle_body(issyk_kul))

    assert await catalog(client, region_id=naryn) == [a["id"]]
    assert await catalog(client, region_id=issyk_kul) == [b["id"]]
    assert await catalog(client) == [a["id"], b["id"]]


@pytest.mark.parametrize("path", ["/vehicles?region_id=0", "/vehicles/0", "/vehicles/x"])
async def test_catalog_invalid_params(client: AsyncClient, path: str) -> None:
    """Неверные параметры → 422."""
    assert_error(await client.get(path), 422, "validation_error")


async def test_unknown_vehicle(client: AsyncClient) -> None:
    """Несуществующая машина → 404."""
    assert_error(await client.get("/vehicles/999999"), 404, "not_found")
