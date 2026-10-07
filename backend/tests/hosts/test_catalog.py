"""Каталог хозяев GET /hosts (?region_id, ?tag_ids) и скрытие заблокированных хозяев.

R-03 — в каталоге только kyc_status = 'approved' и is_published; R-09 — фильтр «по занятиям» —
это фильтр по тегам активных занятий; R-10 — скрытое занятие в фильтре не участвует.
Решение пользователя (Step 8): заблокированный хозяин (users.status = blocked) скрыт из
каталога и публичного профиля.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hosts.models import HostProfile
from app.modules.users.models import KycStatus, User, UserStatus
from tests.activities.test_activities import (
    activity_body,
    create_activity,
    host_with_headers,
    tag_ids,
)
from tests.admin.helpers import assert_error, region_id_by_name
from tests.hosts.test_hosts import PUBLIC_FIELDS, base_body, create_host, set_kyc, user_with_headers


async def catalog(client: AsyncClient, **params: Any) -> list[int]:
    response = await client.get("/hosts", params=params)
    assert response.status_code == 200, response.text
    return [int(h["user_id"]) for h in response.json()]


async def block(db_session: AsyncSession, user_id: int) -> None:
    await db_session.execute(
        update(User).where(User.id == user_id).values(status=UserStatus.BLOCKED)
    )
    await db_session.commit()


async def test_catalog_shape(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-06: в каталоге публичный вид хозяина, без адреса и координат."""
    await host_with_headers(db_session, client)
    response = await client.get("/hosts")
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data) == 1
    assert set(data[0]) == PUBLIC_FIELDS


@pytest.mark.parametrize(
    "kyc",
    [KycStatus.NOT_STARTED, KycStatus.PENDING, KycStatus.REJECTED, KycStatus.RESUBMISSION],
)
async def test_catalog_only_approved(
    client: AsyncClient, db_session: AsyncSession, kyc: KycStatus
) -> None:
    """R-03: хозяин без одобренного KYC в каталоге не виден."""
    approved, _ = await host_with_headers(db_session, client)
    await host_with_headers(db_session, client, kyc=kyc)
    assert await catalog(client) == [approved.id]


async def test_catalog_only_published(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-03: неопубликованный профиль не виден ни в каталоге, ни в списке занятий."""
    published, _ = await host_with_headers(db_session, client)
    hidden, headers = await host_with_headers(db_session, client)
    await create_activity(client, headers, activity_body())
    await db_session.execute(
        update(HostProfile).where(HostProfile.user_id == hidden.id).values(is_published=False)
    )
    await db_session.commit()
    assert await catalog(client) == [published.id]
    assert_error(await client.get(f"/hosts/{hidden.id}/activities"), 404, "not_found")


async def test_blocked_host_hidden(client: AsyncClient, db_session: AsyncSession) -> None:
    """Заблокированный хозяин скрыт из каталога, публичного профиля и списка занятий."""
    active, _ = await host_with_headers(db_session, client)
    blocked, headers = await host_with_headers(db_session, client)
    await create_activity(client, headers, activity_body())
    await block(db_session, blocked.id)

    assert await catalog(client) == [active.id]
    assert_error(await client.get(f"/hosts/{blocked.id}"), 404, "not_found")
    assert_error(await client.get(f"/hosts/{blocked.id}/activities"), 404, "not_found")


async def test_catalog_region_filter(client: AsyncClient, db_session: AsyncSession) -> None:
    """Фильтр по региону отдаёт только хозяев этого региона."""
    region_a = await region_id_by_name(client, "Нарынская область")
    region_b = await region_id_by_name(client, "Иссык-Кульская область")
    hosts: dict[int, int] = {}
    for region_id in (region_a, region_b):
        user, headers = await user_with_headers(db_session, client)
        await create_host(client, headers, base_body(region_id))
        await set_kyc(db_session, user.id, KycStatus.APPROVED)
        hosts[region_id] = user.id

    assert await catalog(client, region_id=region_a) == [hosts[region_a]]
    assert await catalog(client, region_id=region_b) == [hosts[region_b]]
    assert await catalog(client) == sorted(hosts.values())


async def test_catalog_tag_filter(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-09: фильтр по тегам — хозяева с активным занятием хотя бы с одним из тегов."""
    t1, t2, t3, *_ = await tag_ids(client)
    h1, headers1 = await host_with_headers(db_session, client)
    h2, headers2 = await host_with_headers(db_session, client)
    await host_with_headers(db_session, client)  # без занятий
    await create_activity(client, headers1, activity_body(tag_ids=[t1]))
    await create_activity(client, headers2, activity_body(tag_ids=[t2]))

    assert await catalog(client, tag_ids=[t1]) == [h1.id]
    assert await catalog(client, tag_ids=[t2]) == [h2.id]
    assert await catalog(client, tag_ids=[t1, t2]) == [h1.id, h2.id]
    assert await catalog(client, tag_ids=[t3]) == []


async def test_catalog_tag_filter_ignores_inactive(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-09, R-10: скрытое занятие в фильтре по тегам не учитывается."""
    t1, *_ = await tag_ids(client)
    host, headers = await host_with_headers(db_session, client)
    created = await create_activity(client, headers, activity_body(tag_ids=[t1]))
    assert await catalog(client, tag_ids=[t1]) == [host.id]

    await client.post(f"/activities/{created['id']}/deactivate", headers=headers)
    assert await catalog(client, tag_ids=[t1]) == []


async def test_catalog_tag_filter_only_visible_hosts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-03, R-09: фильтр по тегам не показывает хозяина без одобренного KYC."""
    t1, *_ = await tag_ids(client)
    _, headers = await host_with_headers(db_session, client, kyc=KycStatus.PENDING)
    await create_activity(client, headers, activity_body(tag_ids=[t1]))
    assert await catalog(client, tag_ids=[t1]) == []


async def test_catalog_tag_and_region(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-09: фильтры по региону и тегам работают вместе."""
    t1, *_ = await tag_ids(client)
    naryn = await region_id_by_name(client, "Нарынская область")
    issyk_kul = await region_id_by_name(client, "Иссык-Кульская область")
    hosts: dict[int, int] = {}
    for region_id in (naryn, issyk_kul):
        user, headers = await user_with_headers(db_session, client)
        await create_host(client, headers, base_body(region_id))
        await set_kyc(db_session, user.id, KycStatus.APPROVED)
        await create_activity(client, headers, activity_body(tag_ids=[t1]))
        hosts[region_id] = user.id

    assert await catalog(client, region_id=naryn, tag_ids=[t1]) == [hosts[naryn]]


@pytest.mark.parametrize("params", [{"region_id": 0}, {"tag_ids": 0}, {"tag_ids": "x"}])
async def test_catalog_invalid_params(client: AsyncClient, params: dict[str, Any]) -> None:
    """Неверные параметры фильтра → 422."""
    response = await client.get("/hosts", params=params)
    assert_error(response, 422, "validation_error")
