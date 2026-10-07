"""Занятия хозяина: POST/PATCH /activities, деактивация, GET /activities/my, /hosts/{id}/activities.

R-08 — занятие создаёт хозяин, цена за человека > 0; R-09 — теги только из админского списка;
R-10 — занятие не удаляется, а скрывается (is_active = false); R-03 — публично занятия видны
только у видимого хозяина.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import KycStatus, User
from tests.admin.helpers import assert_error
from tests.hosts.test_hosts import (
    base_body,
    create_host,
    first_region_id,
    set_kyc,
    user_with_headers,
)

OWNER_FIELDS = {
    "id",
    "host_id",
    "name",
    "description",
    "price_per_person",
    "tags",
    "is_active",
    "created_at",
    "updated_at",
}
PUBLIC_FIELDS = {"id", "host_id", "name", "description", "price_per_person", "tags"}


async def tag_ids(client: AsyncClient) -> list[int]:
    response = await client.get("/tags")
    assert response.status_code == 200, response.text
    ids = [int(t["id"]) for t in response.json()]
    assert len(ids) >= 3, "в сиде меньше трёх тегов"
    return ids


async def host_with_headers(
    db_session: AsyncSession, client: AsyncClient, *, kyc: KycStatus = KycStatus.APPROVED
) -> tuple[User, dict[str, str]]:
    user, headers = await user_with_headers(db_session, client)
    await create_host(client, headers, base_body(await first_region_id(client)))
    await set_kyc(db_session, user.id, kyc)
    return user, headers


def activity_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Конная прогулка",
        "description": "Два часа по джайлоо",
        "price_per_person": "800.00",
    }
    body.update(overrides)
    return body


async def create_activity(
    client: AsyncClient, headers: dict[str, str], body: dict[str, Any]
) -> dict[str, Any]:
    response = await client.post("/activities", headers=headers, json=body)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


async def activity_rows(db_session: AsyncSession) -> int:
    count: int = await db_session.scalar(text("SELECT count(*) FROM activities")) or 0
    return count


# --- без токена ---


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/activities"),
        ("GET", "/activities/my"),
        ("PATCH", "/activities/1"),
        ("POST", "/activities/1/deactivate"),
    ],
)
async def test_activities_require_auth(client: AsyncClient, method: str, path: str) -> None:
    """Управление занятиями без токена → 401."""
    response = await client.request(method, path, json={})
    assert_error(response, 401, "unauthorized")


# --- создание (R-08, R-09) ---


async def test_create_activity(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-08, R-09: хозяин создаёт занятие с ценой > 0 и тегами из админского списка."""
    user, headers = await host_with_headers(db_session, client)
    t1, t2, *_ = await tag_ids(client)

    data = await create_activity(client, headers, activity_body(tag_ids=[t2, t1, t1]))

    assert set(data) == OWNER_FIELDS
    assert data["host_id"] == user.id
    assert data["name"] == "Конная прогулка"
    assert data["description"] == "Два часа по джайлоо"
    assert data["price_per_person"] == "800.00"
    assert data["is_active"] is True
    assert [t["id"] for t in data["tags"]] == sorted([t1, t2])
    assert all(t["name"] for t in data["tags"])


async def test_create_minimal_activity(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-08: описание и теги необязательны."""
    _, headers = await host_with_headers(db_session, client)

    data = await create_activity(client, headers, {"name": "Чай", "price_per_person": "1"})

    assert data["description"] is None
    assert data["tags"] == []
    assert data["price_per_person"] == "1.00"


async def test_host_without_kyc_can_create(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-08: создавать занятия может любой хозяин; KYC влияет только на видимость (R-03)."""
    _, headers = await host_with_headers(db_session, client, kyc=KycStatus.NOT_STARTED)
    await create_activity(client, headers, activity_body())


@pytest.mark.parametrize("price", ["0", "0.00", "-1", "-0.01"])
async def test_price_must_be_positive(
    client: AsyncClient, db_session: AsyncSession, price: str
) -> None:
    """R-08: бесплатных занятий нет — цена <= 0 отклоняется."""
    _, headers = await host_with_headers(db_session, client)
    response = await client.post(
        "/activities", headers=headers, json=activity_body(price_per_person=price)
    )
    assert_error(response, 422, "validation_error")
    assert await activity_rows(db_session) == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"name": "   "},
        {"name": None},
        {"price_per_person": None},
        {"price_per_person": "1.001"},
        {"tag_ids": [0]},
    ],
)
async def test_create_invalid_body(
    client: AsyncClient, db_session: AsyncSession, overrides: dict[str, Any]
) -> None:
    """R-08: название и цена обязательны."""
    _, headers = await host_with_headers(db_session, client)
    response = await client.post("/activities", headers=headers, json=activity_body(**overrides))
    assert_error(response, 422, "validation_error")


async def test_unknown_tag_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-09: тег не из админского списка → 404, занятие не создаётся."""
    _, headers = await host_with_headers(db_session, client)
    known = await tag_ids(client)
    response = await client.post(
        "/activities", headers=headers, json=activity_body(tag_ids=[known[0], 999])
    )
    assert_error(response, 404, "not_found")
    assert await activity_rows(db_session) == 0


async def test_non_host_cannot_create(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-02, R-08: без профиля хозяина занятие не создать."""
    _, headers = await user_with_headers(db_session, client)
    response = await client.post("/activities", headers=headers, json=activity_body())
    assert_error(response, 403, "permission_denied")
    assert await activity_rows(db_session) == 0


# --- изменение ---


async def test_update_activity(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-08, R-09: владелец меняет название, цену, описание и теги."""
    _, headers = await host_with_headers(db_session, client)
    t1, t2, t3, *_ = await tag_ids(client)
    created = await create_activity(client, headers, activity_body(tag_ids=[t1, t2]))

    response = await client.patch(
        f"/activities/{created['id']}",
        headers=headers,
        json={"name": "Рыбалка", "price_per_person": "1200.50", "tag_ids": [t3]},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["name"] == "Рыбалка"
    assert data["price_per_person"] == "1200.50"
    assert data["description"] == "Два часа по джайлоо"
    assert [t["id"] for t in data["tags"]] == [t3]


async def test_update_nulls(client: AsyncClient, db_session: AsyncSession) -> None:
    """null в name/price/tag_ids — не менять; description null — очистить; [] снимает теги."""
    _, headers = await host_with_headers(db_session, client)
    t1, *_ = await tag_ids(client)
    created = await create_activity(client, headers, activity_body(tag_ids=[t1]))
    url = f"/activities/{created['id']}"

    response = await client.patch(
        url,
        headers=headers,
        json={"name": None, "price_per_person": None, "tag_ids": None, "description": None},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["name"] == created["name"]
    assert data["price_per_person"] == created["price_per_person"]
    assert [t["id"] for t in data["tags"]] == [t1]
    assert data["description"] is None

    response = await client.patch(url, headers=headers, json={"tag_ids": []})
    assert response.status_code == 200, response.text
    assert response.json()["tags"] == []
    assert response.json()["updated_at"] > data["updated_at"]  # менялись только теги


async def test_update_price_must_be_positive(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-08: и при изменении цена > 0."""
    _, headers = await host_with_headers(db_session, client)
    created = await create_activity(client, headers, activity_body())
    response = await client.patch(
        f"/activities/{created['id']}", headers=headers, json={"price_per_person": "0"}
    )
    assert_error(response, 422, "validation_error")


async def test_update_unknown_tag(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-09: неверный тег при изменении → 404, старые теги остаются."""
    _, headers = await host_with_headers(db_session, client)
    t1, *_ = await tag_ids(client)
    created = await create_activity(client, headers, activity_body(tag_ids=[t1]))
    url = f"/activities/{created['id']}"

    response = await client.patch(url, headers=headers, json={"tag_ids": [999]})
    assert_error(response, 404, "not_found")

    mine = (await client.get("/activities/my", headers=headers)).json()
    assert [t["id"] for t in mine[0]["tags"]] == [t1]


async def test_only_owner_manages(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-08: чужое занятие нельзя менять и скрывать — 404, как несуществующее."""
    _, owner = await host_with_headers(db_session, client)
    _, other = await host_with_headers(db_session, client)
    created = await create_activity(client, owner, activity_body())
    url = f"/activities/{created['id']}"

    assert_error(await client.patch(url, headers=other, json={"name": "X"}), 404, "not_found")
    assert_error(await client.post(f"{url}/deactivate", headers=other), 404, "not_found")
    assert_error(await client.patch("/activities/999999", headers=owner, json={}), 404, "not_found")

    mine = (await client.get("/activities/my", headers=owner)).json()
    assert mine[0]["name"] == created["name"]
    assert mine[0]["is_active"] is True


# --- деактивация (R-10) ---


async def test_deactivate(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-10: деактивация ставит is_active = false, строка остаётся; повтор — без ошибки."""
    _, headers = await host_with_headers(db_session, client)
    created = await create_activity(client, headers, activity_body())
    url = f"/activities/{created['id']}/deactivate"

    for _ in range(2):
        response = await client.post(url, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["is_active"] is False

    assert await activity_rows(db_session) == 1


async def test_no_delete_endpoint(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-10: эндпоинта удаления нет."""
    _, headers = await host_with_headers(db_session, client)
    created = await create_activity(client, headers, activity_body())
    response = await client.delete(f"/activities/{created['id']}", headers=headers)
    assert response.status_code == 405
    assert await activity_rows(db_session) == 1


async def test_my_activities_include_inactive(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-10: в своём списке хозяин видит и скрытые занятия; чужих там нет."""
    _, headers = await host_with_headers(db_session, client)
    _, other = await host_with_headers(db_session, client)
    a = await create_activity(client, headers, activity_body(name="A"))
    b = await create_activity(client, headers, activity_body(name="B"))
    await create_activity(client, other, activity_body(name="C"))
    await client.post(f"/activities/{a['id']}/deactivate", headers=headers)

    response = await client.get("/activities/my", headers=headers)

    assert response.status_code == 200, response.text
    data = response.json()
    assert [(x["id"], x["is_active"]) for x in data] == [(a["id"], False), (b["id"], True)]
    assert set(data[0]) == OWNER_FIELDS


# --- публичный список занятий хозяина ---


async def test_public_host_activities(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-10: публично видны только активные занятия хозяина, без служебных полей."""
    user, headers = await host_with_headers(db_session, client)
    a = await create_activity(client, headers, activity_body(name="A"))
    b = await create_activity(client, headers, activity_body(name="B"))
    await client.post(f"/activities/{a['id']}/deactivate", headers=headers)

    response = await client.get(f"/hosts/{user.id}/activities")

    assert response.status_code == 200, response.text
    data = response.json()
    assert [x["id"] for x in data] == [b["id"]]
    assert set(data[0]) == PUBLIC_FIELDS


@pytest.mark.parametrize("kyc", [KycStatus.NOT_STARTED, KycStatus.PENDING, KycStatus.REJECTED])
async def test_public_activities_hidden_without_kyc(
    client: AsyncClient, db_session: AsyncSession, kyc: KycStatus
) -> None:
    """R-03: у хозяина без одобренного KYC занятия публично не видны — 404."""
    user, headers = await host_with_headers(db_session, client, kyc=kyc)
    await create_activity(client, headers, activity_body())
    assert_error(await client.get(f"/hosts/{user.id}/activities"), 404, "not_found")


async def test_public_activities_unknown_host(client: AsyncClient) -> None:
    """Несуществующий хозяин → 404."""
    assert_error(await client.get("/hosts/999999/activities"), 404, "not_found")
