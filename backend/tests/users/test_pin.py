"""PIN заселения: выдача при одобрении KYC, GET /users/me/pin, POST /users/me/pin/regenerate.

R-23 (решение MVP, не окончательное): PIN один на пользователя, 6 цифр, выдаётся при одобрении
KYC, хранится открыто и всегда виден владельцу; можно перевыпустить. R-39: уведомление
kyc_approved без самого PIN.
"""

import re

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.models import Notification
from app.modules.users.models import KycStatus, User, UserRole
from tests.admin.helpers import assert_error
from tests.hosts.test_hosts import set_kyc
from tests.kyc.test_kyc import submit, user_with_headers

PIN_RE = re.compile(r"^[0-9]{6}$")


async def approved_via_api(
    db_session: AsyncSession, client: AsyncClient
) -> tuple[User, dict[str, str]]:
    user, headers = await user_with_headers(db_session, client)
    kyc = await submit(client, headers)
    _, moderator = await user_with_headers(db_session, client, UserRole.MODERATOR)
    response = await client.post(f"/kyc/{kyc['id']}/approve", headers=moderator)
    assert response.status_code == 200, response.text
    return user, headers


async def stored_pin(db_session: AsyncSession, user_id: int) -> str | None:
    pin: str | None = await db_session.scalar(select(User.checkin_pin).where(User.id == user_id))
    return pin


@pytest.mark.parametrize(
    ("method", "path"), [("GET", "/users/me/pin"), ("POST", "/users/me/pin/regenerate")]
)
async def test_pin_requires_auth(client: AsyncClient, method: str, path: str) -> None:
    """Без токена → 401."""
    assert_error(await client.request(method, path), 401, "unauthorized")


async def test_pin_issued_on_kyc_approval(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-23: при одобрении KYC выдаётся PIN из 6 цифр, владелец видит его сколько угодно раз."""
    user, headers = await approved_via_api(db_session, client)

    first = await client.get("/users/me/pin", headers=headers)
    second = await client.get("/users/me/pin", headers=headers)

    assert first.status_code == 200, first.text
    pin = first.json()["pin"]
    assert PIN_RE.match(pin)
    assert second.json() == {"pin": pin}
    assert first.headers["cache-control"] == "no-store"
    assert await stored_pin(db_session, user.id) == pin


async def test_pin_not_in_notification_or_profile(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-39, R-23: PIN нет в уведомлении kyc_approved и в GET /users/me."""
    user, headers = await approved_via_api(db_session, client)
    pin = (await client.get("/users/me/pin", headers=headers)).json()["pin"]

    notifications = (
        await db_session.scalars(select(Notification).where(Notification.user_id == user.id))
    ).all()
    assert [n.type for n in notifications] == ["kyc_approved"]
    assert pin not in str(notifications[0].payload)
    assert pin not in (await client.get("/users/me", headers=headers)).text
    assert pin not in (await client.get("/notifications", headers=headers)).text


@pytest.mark.parametrize(
    "kyc", [KycStatus.NOT_STARTED, KycStatus.PENDING, KycStatus.REJECTED, KycStatus.RESUBMISSION]
)
async def test_no_pin_without_kyc(
    client: AsyncClient, db_session: AsyncSession, kyc: KycStatus
) -> None:
    """R-23: без одобренного KYC PIN нет (404) и перевыпустить нельзя (403)."""
    user, headers = await user_with_headers(db_session, client)
    await set_kyc(db_session, user.id, kyc)
    assert_error(await client.get("/users/me/pin", headers=headers), 404, "not_found")
    response = await client.post("/users/me/pin/regenerate", headers=headers)
    assert_error(response, 403, "permission_denied")
    assert await stored_pin(db_session, user.id) is None


async def test_rejection_issues_no_pin(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-23: отклонённый KYC PIN не выдаёт."""
    user, headers = await user_with_headers(db_session, client)
    kyc = await submit(client, headers)
    _, moderator = await user_with_headers(db_session, client, UserRole.MODERATOR)
    response = await client.post(
        f"/kyc/{kyc['id']}/reject", headers=moderator, json={"reason": "Размыто"}
    )
    assert response.status_code == 200, response.text
    assert await stored_pin(db_session, user.id) is None


async def test_regenerate_pin(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-23: перевыпуск даёт новый PIN; старый больше не хранится."""
    user, headers = await approved_via_api(db_session, client)
    old = (await client.get("/users/me/pin", headers=headers)).json()["pin"]

    pins = set()
    for _ in range(3):
        response = await client.post("/users/me/pin/regenerate", headers=headers)
        assert response.status_code == 200, response.text
        pins.add(response.json()["pin"])
        assert response.headers["cache-control"] == "no-store"
        assert PIN_RE.match(response.json()["pin"])

    current = (await client.get("/users/me/pin", headers=headers)).json()["pin"]
    assert current == response.json()["pin"]
    assert await stored_pin(db_session, user.id) == current
    assert pins - {old}, "за три перевыпуска PIN ни разу не сменился"


async def test_pin_only_own(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-23: каждый видит только свой PIN."""
    a, headers_a = await approved_via_api(db_session, client)
    b, headers_b = await approved_via_api(db_session, client)
    pin_a = (await client.get("/users/me/pin", headers=headers_a)).json()["pin"]
    pin_b = (await client.get("/users/me/pin", headers=headers_b)).json()["pin"]
    assert pin_a == await stored_pin(db_session, a.id)
    assert pin_b == await stored_pin(db_session, b.id)
