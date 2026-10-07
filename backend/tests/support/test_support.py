"""Поддержка: /support/tickets.

R-27 — жалоба = тикет category='complaint' со свободным текстом, хозяин жалуется на гостя так же;
R-22 — хозяин, который не может отменить бронь, идёт в поддержку; владелец видит только свои
тикеты, модератор / админ — очередь и меняет статус.
"""

from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.support.models import SupportTicket, TicketStatus
from app.modules.users.models import UserRole
from tests.admin.helpers import assert_error
from tests.bookings.test_checkin import confirmed_stay
from tests.bookings.test_stay_bookings import guest_with_headers, make_host
from tests.kyc.test_kyc import user_with_headers

TICKET_FIELDS = {
    "id",
    "user_id",
    "booking_id",
    "category",
    "description",
    "status",
    "assigned_to",
    "created_at",
    "updated_at",
}


async def create(client: AsyncClient, headers: dict[str, str], **body: Any) -> Response:
    body.setdefault("category", "booking_problem")
    body.setdefault("description", "Что-то пошло не так")
    return await client.post("/support/tickets", headers=headers, json=body)


async def staff(
    db_session: AsyncSession, client: AsyncClient, role: UserRole = UserRole.MODERATOR
) -> tuple[int, dict[str, str]]:
    user, headers = await user_with_headers(db_session, client, role)
    return user.id, headers


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("POST", "/support/tickets"),
        ("GET", "/support/tickets/my"),
        ("GET", "/support/tickets"),
        ("GET", "/support/tickets/1"),
        ("POST", "/support/tickets/1/status"),
    ],
)
async def test_requires_auth(client: AsyncClient, method: str, url: str) -> None:
    assert_error(await client.request(method, url, json={}), 401, "unauthorized")


async def test_create_complaint_free_text(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-27: жалоба со свободным текстом; статус new, исполнителя нет."""
    user, headers = await guest_with_headers(db_session, client)

    response = await create(
        client,
        headers,
        category="complaint",
        description="  Хозяин требовал деньги мимо платформы  ",
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert set(data) == TICKET_FIELDS
    assert (data["category"], data["status"], data["assigned_to"]) == ("complaint", "new", None)
    assert data["description"] == "Хозяин требовал деньги мимо платформы"
    assert data["user_id"] == user.id and data["booking_id"] is None


async def test_host_complains_about_guest(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-27, R-22: хозяин жалуется на гостя по своей брони так же, как гость."""
    _, host_headers, _, booking = await confirmed_stay(db_session, client)
    response = await create(
        client,
        host_headers,
        category="complaint",
        description="Гость не приехал и не отвечает",
        booking_id=booking["id"],
    )
    assert response.status_code == 201, response.text
    assert response.json()["booking_id"] == booking["id"]


async def test_guest_links_own_booking(client: AsyncClient, db_session: AsyncSession) -> None:
    _, _, headers, booking = await confirmed_stay(db_session, client)
    assert (await create(client, headers, booking_id=booking["id"])).status_code == 201


async def test_foreign_or_unknown_booking(client: AsyncClient, db_session: AsyncSession) -> None:
    """Привязать чужую или несуществующую бронь нельзя → 404, тикета нет."""
    _, _, _, booking = await confirmed_stay(db_session, client)
    _, stranger = await guest_with_headers(db_session, client)
    for booking_id in (booking["id"], 999999):
        assert_error(await create(client, stranger, booking_id=booking_id), 404, "not_found")
    assert await db_session.scalar(text("SELECT count(*) FROM support_tickets")) == 0


@pytest.mark.parametrize(
    "body",
    [
        {"category": "", "description": "x"},
        {"category": "x" * 51, "description": "x"},
        {"category": "complaint", "description": ""},
        {"category": "complaint", "description": "   "},
        {"category": "complaint", "description": "x" * 4001},
        {"category": "complaint"},
        {"description": "x"},
        {"category": "complaint", "description": "x", "booking_id": 0},
    ],
)
async def test_create_invalid(
    client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    _, headers = await guest_with_headers(db_session, client)
    response = await client.post("/support/tickets", headers=headers, json=body)
    assert_error(response, 422, "validation_error")


async def test_owner_sees_only_own(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /my — только свои, свежие сверху; чужой тикет по id → 404."""
    _, headers = await guest_with_headers(db_session, client)
    _, other = await guest_with_headers(db_session, client)
    first = (await create(client, headers, description="первый")).json()
    second = (await create(client, headers, description="второй")).json()
    foreign = (await create(client, other)).json()

    mine = (await client.get("/support/tickets/my", headers=headers)).json()
    assert [t["id"] for t in mine] == [second["id"], first["id"]]

    own = await client.get(f"/support/tickets/{first['id']}", headers=headers)
    assert own.status_code == 200 and own.json()["id"] == first["id"]
    assert_error(
        await client.get(f"/support/tickets/{foreign['id']}", headers=headers), 404, "not_found"
    )
    assert_error(await client.get("/support/tickets/999999", headers=headers), 404, "not_found")


async def test_user_cannot_use_queue_or_change_status(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers = await guest_with_headers(db_session, client)
    ticket = (await create(client, headers)).json()
    assert_error(await client.get("/support/tickets", headers=headers), 403, "permission_denied")
    response = await client.post(
        f"/support/tickets/{ticket['id']}/status", headers=headers, json={"status": "closed"}
    )
    assert_error(response, 403, "permission_denied")
    stored = await db_session.scalar(select(SupportTicket.status))
    assert stored == TicketStatus.NEW


@pytest.mark.parametrize("role", [UserRole.MODERATOR, UserRole.ADMIN])
async def test_staff_queue_oldest_first_with_filter(
    client: AsyncClient, db_session: AsyncSession, role: UserRole
) -> None:
    """Очередь: старые первыми, видны тикеты всех пользователей, фильтр по статусу."""
    _, mod = await staff(db_session, client, role)
    _, a = await guest_with_headers(db_session, client)
    _, b = await guest_with_headers(db_session, client)
    t1 = (await create(client, a)).json()
    t2 = (await create(client, b)).json()
    t3 = (await create(client, a)).json()
    await client.post(
        f"/support/tickets/{t2['id']}/status", headers=mod, json={"status": "resolved"}
    )

    queue = (await client.get("/support/tickets", headers=mod)).json()
    assert [t["id"] for t in queue] == [t1["id"], t2["id"], t3["id"]]

    new = (await client.get("/support/tickets?status=new", headers=mod)).json()
    assert [t["id"] for t in new] == [t1["id"], t3["id"]]
    assert_error(
        await client.get("/support/tickets?status=bogus", headers=mod), 422, "validation_error"
    )


async def test_staff_status_cycle(client: AsyncClient, db_session: AsyncSession) -> None:
    """Цикл тикета: new → in_progress → waiting_reply → resolved → closed; исполнитель — первый."""
    mod_id, mod = await staff(db_session, client)
    admin_id, admin = await staff(db_session, client, UserRole.ADMIN)
    _, headers = await guest_with_headers(db_session, client)
    ticket = (await create(client, headers)).json()

    for status, actor in (
        ("in_progress", mod),
        ("waiting_reply", mod),
        ("resolved", admin),
        ("closed", admin),
    ):
        response = await client.post(
            f"/support/tickets/{ticket['id']}/status", headers=actor, json={"status": status}
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == status
        assert response.json()["assigned_to"] == mod_id  # закреплён за первым обработавшим

    seen = (await client.get(f"/support/tickets/{ticket['id']}", headers=headers)).json()
    assert seen["status"] == "closed"
    assert seen["updated_at"] > seen["created_at"]
    assert admin_id != mod_id


async def test_staff_reads_any_ticket(client: AsyncClient, db_session: AsyncSession) -> None:
    _, mod = await staff(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    ticket = (await create(client, headers)).json()
    response = await client.get(f"/support/tickets/{ticket['id']}", headers=mod)
    assert response.status_code == 200 and response.json()["id"] == ticket["id"]


async def test_status_invalid_and_unknown(client: AsyncClient, db_session: AsyncSession) -> None:
    _, mod = await staff(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    ticket = (await create(client, headers)).json()
    for body in ({}, {"status": "bogus"}):
        response = await client.post(
            f"/support/tickets/{ticket['id']}/status", headers=mod, json=body
        )
        assert_error(response, 422, "validation_error")
    response = await client.post(
        "/support/tickets/999999/status", headers=mod, json={"status": "closed"}
    )
    assert_error(response, 404, "not_found")


async def test_host_can_create_ticket(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-22: хозяин создаёт обращение без брони."""
    _, headers = await make_host(db_session, client)
    assert (await create(client, headers, category="emergency")).status_code == 201
