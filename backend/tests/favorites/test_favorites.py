"""Избранное: /favorites.

R-38 — пользователь управляет только своим избранным; запись указывает ровно на один объект
(хозяин, место или машина); повторно добавить нельзя.
"""

from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import KycStatus
from tests.admin.helpers import assert_error
from tests.bookings.test_stay_bookings import guest_with_headers, make_host
from tests.bookings.test_vehicle_bookings import make_vehicle
from tests.hosts.test_hosts import set_kyc

FAVORITE_FIELDS = {"id", "host_id", "place_id", "vehicle_id", "created_at"}


async def add(client: AsyncClient, headers: dict[str, str], **body: Any) -> Response:
    return await client.post("/favorites", headers=headers, json=body)


async def place_id(client: AsyncClient) -> int:
    places = (await client.get("/places")).json()
    value: int = places[0]["id"]
    return value


async def count(db_session: AsyncSession) -> int:
    value: int = await db_session.scalar(text("SELECT count(*) FROM favorites"))
    return value


@pytest.mark.parametrize(
    ("method", "url"), [("POST", "/favorites"), ("GET", "/favorites"), ("DELETE", "/favorites/1")]
)
async def test_requires_auth(client: AsyncClient, method: str, url: str) -> None:
    assert_error(await client.request(method, url), 401, "unauthorized")


async def test_add_each_kind(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-38: можно добавить хозяина, место и машину; в записи заполнен один объект."""
    host, _ = await make_host(db_session, client)
    _, _, vehicle_id = await make_vehicle(db_session, client)
    place = await place_id(client)
    _, headers = await guest_with_headers(db_session, client)

    for body, key in (
        ({"host_id": host.id}, "host_id"),
        ({"place_id": place}, "place_id"),
        ({"vehicle_id": vehicle_id}, "vehicle_id"),
    ):
        response = await add(client, headers, **body)
        assert response.status_code == 201, response.text
        data = response.json()
        assert set(data) == FAVORITE_FIELDS
        assert data[key] == body[key]
        assert [data[k] for k in ("host_id", "place_id", "vehicle_id") if k != key] == [None, None]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"host_id": 1, "place_id": 1},
        {"host_id": 1, "vehicle_id": 1},
        {"place_id": 1, "vehicle_id": 1},
        {"host_id": 1, "place_id": 1, "vehicle_id": 1},
        {"host_id": None, "place_id": None},
        {"host_id": 0},
        {"vehicle_id": "x"},
    ],
)
async def test_exactly_one_target(
    client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    """R-38: ровно один объект → иначе 422, записи нет."""
    _, headers = await guest_with_headers(db_session, client)
    assert_error(await add(client, headers, **body), 422, "validation_error")
    assert await count(db_session) == 0


async def test_duplicate_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-38: тот же объект второй раз → 409; другой пользователь добавить может."""
    host, _ = await make_host(db_session, client)
    _, _, vehicle_id = await make_vehicle(db_session, client)
    place = await place_id(client)
    _, headers = await guest_with_headers(db_session, client)
    _, other = await guest_with_headers(db_session, client)

    for body in ({"host_id": host.id}, {"place_id": place}, {"vehicle_id": vehicle_id}):
        assert (await add(client, headers, **body)).status_code == 201
        assert_error(await add(client, headers, **body), 409, "conflict")
        assert (await add(client, other, **body)).status_code == 201
    assert await count(db_session) == 6


async def test_target_must_exist_and_be_visible(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Несуществующий или скрытый объект (хозяин без одобренного KYC) → 404."""
    hidden, _ = await make_host(db_session, client)
    await set_kyc(db_session, hidden.id, KycStatus.PENDING)
    _, headers = await guest_with_headers(db_session, client)
    for body in (
        {"host_id": hidden.id},
        {"host_id": 999999},
        {"place_id": 999999},
        {"vehicle_id": 999999},
    ):
        assert_error(await add(client, headers, **body), 404, "not_found")
    assert await count(db_session) == 0


async def test_list_only_own_newest_first(client: AsyncClient, db_session: AsyncSession) -> None:
    host, _ = await make_host(db_session, client)
    _, _, vehicle_id = await make_vehicle(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    _, other = await guest_with_headers(db_session, client)
    first = (await add(client, headers, host_id=host.id)).json()
    second = (await add(client, headers, vehicle_id=vehicle_id)).json()
    await add(client, other, host_id=host.id)

    mine = (await client.get("/favorites", headers=headers)).json()
    assert [f["id"] for f in mine] == [second["id"], first["id"]]
    assert (await client.get("/favorites", headers=other)).json() != mine
    _, empty = await guest_with_headers(db_session, client)
    assert (await client.get("/favorites", headers=empty)).json() == []


async def test_delete_own(client: AsyncClient, db_session: AsyncSession) -> None:
    """Удаление своей записи → 204; после этого объект можно добавить снова."""
    host, _ = await make_host(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    favorite = (await add(client, headers, host_id=host.id)).json()

    response = await client.delete(f"/favorites/{favorite['id']}", headers=headers)

    assert response.status_code == 204 and response.content == b""
    assert await count(db_session) == 0
    assert (await add(client, headers, host_id=host.id)).status_code == 201


async def test_delete_foreign_or_unknown(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-38: чужую запись удалить нельзя → 404, запись цела."""
    host, _ = await make_host(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    _, other = await guest_with_headers(db_session, client)
    favorite = (await add(client, headers, host_id=host.id)).json()

    assert_error(
        await client.delete(f"/favorites/{favorite['id']}", headers=other), 404, "not_found"
    )
    assert_error(await client.delete("/favorites/999999", headers=headers), 404, "not_found")
    assert await count(db_session) == 1


async def test_place_deletion_removes_favorites(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """ON DELETE CASCADE: удалённое место исчезает из избранного."""
    place = await place_id(client)
    _, headers = await guest_with_headers(db_session, client)
    await add(client, headers, place_id=place)
    await db_session.execute(text("DELETE FROM places WHERE id = :id"), {"id": place})
    await db_session.commit()
    assert (await client.get("/favorites", headers=headers)).json() == []
