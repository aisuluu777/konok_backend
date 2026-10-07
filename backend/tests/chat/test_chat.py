"""Чат 1-на-1: /conversations.

R-25 — один диалог на пару «гость — хозяин или владелец машины», бронь не нужна,
оригинал сообщения хранится всегда; читать и писать только участники; read_at у получателя.
"""

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.chat.models import Conversation, Message
from tests.admin.helpers import assert_error
from tests.bookings.test_stay_bookings import guest_with_headers, make_host
from tests.bookings.test_vehicle_bookings import make_vehicle
from tests.hosts.test_hosts import user_with_headers


async def open_chat(client: AsyncClient, headers: dict[str, str], provider_id: int) -> Response:
    return await client.post("/conversations", headers=headers, json={"provider_id": provider_id})


async def send(
    client: AsyncClient, headers: dict[str, str], conversation_id: int, body: str = "Привет"
) -> Response:
    return await client.post(
        f"/conversations/{conversation_id}/messages", headers=headers, json={"text": body}
    )


async def messages(
    client: AsyncClient, headers: dict[str, str], conversation_id: int
) -> list[dict[str, Any]]:
    response = await client.get(f"/conversations/{conversation_id}/messages", headers=headers)
    assert response.status_code == 200, response.text
    data: list[dict[str, Any]] = response.json()
    return data


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("POST", "/conversations"),
        ("GET", "/conversations"),
        ("GET", "/conversations/1/messages"),
        ("POST", "/conversations/1/messages"),
    ],
)
async def test_requires_auth(client: AsyncClient, method: str, url: str) -> None:
    assert_error(await client.request(method, url, json={}), 401, "unauthorized")


async def test_open_creates_and_reuses(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-25: диалог создаётся без брони; повторное открытие той же пары — тот же диалог."""
    host, _ = await make_host(db_session, client)
    guest, headers = await guest_with_headers(db_session, client)

    first = await open_chat(client, headers, host.id)
    second = await open_chat(client, headers, host.id)

    assert first.status_code == 200, first.text
    assert first.json()["id"] == second.json()["id"]
    assert (first.json()["guest_id"], first.json()["provider_id"]) == (guest.id, host.id)
    assert first.json()["last_message_at"] is None
    count = await db_session.scalar(text("SELECT count(*) FROM conversations"))
    assert count == 1


async def test_different_pairs_get_different_conversations(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    host, _ = await make_host(db_session, client)
    owner, _, _ = await make_vehicle(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    _, other = await guest_with_headers(db_session, client)
    ids = {
        (await open_chat(client, headers, host.id)).json()["id"],
        (await open_chat(client, headers, owner.id)).json()["id"],  # владелец машины
        (await open_chat(client, other, host.id)).json()["id"],
    }
    assert len(ids) == 3


async def test_parallel_open_single_conversation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    host, _ = await make_host(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    responses = await asyncio.gather(*(open_chat(client, headers, host.id) for _ in range(4)))
    assert {r.status_code for r in responses} == {200}
    assert len({r.json()["id"] for r in responses}) == 1


async def test_self_chat_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-25: диалог с самим собой → 400."""
    host, host_headers = await make_host(db_session, client)
    assert_error(await open_chat(client, host_headers, host.id), 400, "domain_error")
    count = await db_session.scalar(text("SELECT count(*) FROM conversations"))
    assert count == 0


async def test_provider_must_be_host_or_vehicle_owner(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Собеседник — только хозяин или владелец машины; иначе (и несуществующий) → 404."""
    plain, _ = await user_with_headers(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    for provider_id in (plain.id, 999999):
        assert_error(await open_chat(client, headers, provider_id), 404, "not_found")


async def test_blocked_provider_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    host, _ = await make_host(db_session, client)
    await db_session.execute(
        text("UPDATE users SET status = 'blocked' WHERE id = :id"), {"id": host.id}
    )
    await db_session.commit()
    _, headers = await guest_with_headers(db_session, client)
    assert_error(await open_chat(client, headers, host.id), 404, "not_found")


async def test_conversation_before_any_booking(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-25: переписка работает, пока броней нет вообще; пишут оба."""
    host, host_headers = await make_host(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    chat_id = (await open_chat(client, headers, host.id)).json()["id"]

    assert (await send(client, headers, chat_id, "Есть места?")).status_code == 201
    assert (await send(client, host_headers, chat_id, "Да, приезжайте")).status_code == 201

    texts = [m["text"] for m in await messages(client, headers, chat_id)]
    assert texts == ["Есть места?", "Да, приезжайте"]
    assert await db_session.scalar(text("SELECT count(*) FROM bookings")) == 0


async def test_original_text_is_stored(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-25: оригинал хранится как есть (без обрезки внутри), переводных полей нет."""
    host, _ = await make_host(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    chat_id = (await open_chat(client, headers, host.id)).json()["id"]

    response = await send(client, headers, chat_id, "  Салам!\nЖакшысызбы? 🙂  ")

    assert response.status_code == 201, response.text
    assert set(response.json()) == {
        "id",
        "conversation_id",
        "sender_id",
        "text",
        "read_at",
        "created_at",
    }
    stored = await db_session.scalar(select(Message.original_text))
    assert stored == "Салам!\nЖакшысызбы? 🙂"
    message = await db_session.scalar(select(Message))
    assert message is not None
    assert (message.translated_text, message.original_language, message.is_flagged) == (
        None,
        None,
        False,
    )


async def test_non_participant_forbidden(client: AsyncClient, db_session: AsyncSession) -> None:
    """Не участник не читает и не пишет → 403; несуществующий диалог → 404."""
    host, _ = await make_host(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    _, stranger = await guest_with_headers(db_session, client)
    chat_id = (await open_chat(client, headers, host.id)).json()["id"]
    await send(client, headers, chat_id)

    assert_error(
        await client.get(f"/conversations/{chat_id}/messages", headers=stranger),
        403,
        "permission_denied",
    )
    assert_error(await send(client, stranger, chat_id), 403, "permission_denied")
    assert_error(
        await client.get("/conversations/999999/messages", headers=stranger), 404, "not_found"
    )
    assert await db_session.scalar(text("SELECT count(*) FROM messages")) == 1


async def test_read_marks_only_incoming(client: AsyncClient, db_session: AsyncSession) -> None:
    """read_at ставит получатель при чтении; свои сообщения автор не «прочитывает»."""
    host, host_headers = await make_host(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    chat_id = (await open_chat(client, headers, host.id)).json()["id"]
    await send(client, headers, chat_id, "от гостя")

    own = await messages(client, headers, chat_id)  # автор читает свой диалог
    assert own[0]["read_at"] is None

    incoming = await messages(client, host_headers, chat_id)  # получатель
    assert incoming[0]["read_at"] is not None
    assert (await messages(client, headers, chat_id))[0]["read_at"] is not None


async def test_list_conversations_with_unread(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Список — только мои диалоги, свежие сверху, с числом непрочитанных входящих."""
    host, host_headers = await make_host(db_session, client)
    owner, _, _ = await make_vehicle(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    _, other = await guest_with_headers(db_session, client)
    first = (await open_chat(client, headers, host.id)).json()["id"]
    second = (await open_chat(client, headers, owner.id)).json()["id"]
    await open_chat(client, other, host.id)  # чужой диалог
    await send(client, headers, first)
    await send(client, headers, first)
    await send(client, headers, second)

    mine = (await client.get("/conversations", headers=headers)).json()
    assert [(c["id"], c["unread_count"]) for c in mine] == [(second, 0), (first, 0)]

    hosts = (await client.get("/conversations", headers=host_headers)).json()
    assert {(c["id"], c["unread_count"]) for c in hosts} == {(first, 2), (hosts[-1]["id"], 0)}
    assert hosts[0]["id"] == first  # со свежим сообщением выше диалога без сообщений

    await messages(client, host_headers, first)
    after = (await client.get("/conversations", headers=host_headers)).json()
    assert [c["unread_count"] for c in after] == [0, 0]


async def test_last_message_at_updated(client: AsyncClient, db_session: AsyncSession) -> None:
    host, _ = await make_host(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    chat_id = (await open_chat(client, headers, host.id)).json()["id"]
    sent = (await send(client, headers, chat_id)).json()
    conversation = await db_session.scalar(select(Conversation))
    assert conversation is not None
    await db_session.refresh(conversation)
    assert conversation.last_message_at is not None
    assert (
        conversation.last_message_at.isoformat().replace("+00:00", "Z")[:19]
        == sent["created_at"][:19]
    )


@pytest.mark.parametrize(
    "body", [{}, {"text": ""}, {"text": "   "}, {"text": "x" * 4001}, {"text": 5}]
)
async def test_message_invalid(
    client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    host, _ = await make_host(db_session, client)
    _, headers = await guest_with_headers(db_session, client)
    chat_id = (await open_chat(client, headers, host.id)).json()["id"]
    response = await client.post(f"/conversations/{chat_id}/messages", headers=headers, json=body)
    assert_error(response, 422, "validation_error")


@pytest.mark.parametrize("body", [{}, {"provider_id": 0}, {"provider_id": "x"}])
async def test_open_invalid(
    client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    _, headers = await guest_with_headers(db_session, client)
    response = await client.post("/conversations", headers=headers, json=body)
    assert_error(response, 422, "validation_error")
