from fastapi import APIRouter, status
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import Session
from app.errors import bad_request, not_found
from app.models import Complaint, Conversation, HostProfile, Message, User, Vehicle
from app.notify import notify
from app.pagination import Page, Pagination, paginate
from app.schemas.social import (
    ComplaintCreate,
    ComplaintOut,
    ConversationCreate,
    ConversationOut,
    MessageCreate,
    MessageOut,
)
from app.security import CurrentUser
from app.translator import translate

router = APIRouter(tags=["chat"])


async def _my_conversation(session: AsyncSession, me: User, conversation_id: int) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or me.id not in (conversation.guest_id, conversation.owner_id):
        raise not_found("Чат не найден")
    return conversation


@router.post("/conversations")
async def open_conversation(
    data: ConversationCreate, me: CurrentUser, session: Session
) -> ConversationOut:
    """Открыть чат с хозяином / владельцем авто (или вернуть уже существующий)."""
    if data.owner_id == me.id:
        raise bad_request("Нельзя написать самому себе")
    is_owner = await session.scalar(
        select(
            exists().where(HostProfile.user_id == data.owner_id)
            | exists().where(Vehicle.owner_id == data.owner_id)
        )
    )
    if not is_owner:
        raise not_found("Хозяин или владелец авто не найден")
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.guest_id == me.id, Conversation.owner_id == data.owner_id
        )
    )
    if conversation is None:
        conversation = Conversation(guest_id=me.id, owner_id=data.owner_id)
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation, ["guest", "owner"])
    return ConversationOut.model_validate(conversation)


@router.get("/conversations")
async def list_conversations(
    me: CurrentUser, session: Session, page: Pagination
) -> Page[ConversationOut]:
    query = (
        select(Conversation)
        .where(or_(Conversation.guest_id == me.id, Conversation.owner_id == me.id))
        .order_by(Conversation.id.desc())
    )
    return Page[ConversationOut].model_validate(await paginate(session, query, page))


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: int, me: CurrentUser, session: Session, page: Pagination
) -> Page[MessageOut]:
    """Сообщения, новые первыми."""
    await _my_conversation(session, me, conversation_id)
    query = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
    )
    return Page[MessageOut].model_validate(await paginate(session, query, page))


@router.post("/conversations/{conversation_id}/messages", status_code=status.HTTP_201_CREATED)
async def send_message(
    conversation_id: int, data: MessageCreate, me: CurrentUser, session: Session
) -> MessageOut:
    """Сохраняет оригинал и перевод (Azure). Ошибка перевода не мешает отправке."""
    conversation = await _my_conversation(session, me, conversation_id)
    translations = await translate(data.text) or {}
    message = Message(
        conversation_id=conversation_id,
        sender_id=me.id,
        text=data.text,
        text_ky=translations.get("ky"),
        text_ru=translations.get("ru"),
        text_en=translations.get("en"),
    )
    session.add(message)
    await session.flush()
    recipient = conversation.owner_id if me.id == conversation.guest_id else conversation.guest_id
    notify(
        session,
        recipient,
        "new_message",
        {"conversation_id": conversation_id, "message_id": message.id},
    )
    await session.commit()
    await session.refresh(message)
    return MessageOut.model_validate(message)


@router.post("/complaints", status_code=status.HTTP_201_CREATED, tags=["complaints"])
async def create_complaint(
    data: ComplaintCreate, me: CurrentUser, session: Session
) -> ComplaintOut:
    """Жалоба на пользователя или на его сообщение в вашем чате. Разбирает admin."""
    if data.user_id == me.id:
        raise bad_request("Нельзя пожаловаться на себя")
    if await session.get(User, data.user_id) is None:
        raise not_found("Пользователь не найден")
    if data.message_id is not None:
        message = await session.get(Message, data.message_id)
        if message is None or message.sender_id != data.user_id:
            raise not_found("Сообщение не найдено")
        await _my_conversation(session, me, message.conversation_id)
    complaint = Complaint(
        reporter_id=me.id, target_user_id=data.user_id, message_id=data.message_id, text=data.text
    )
    session.add(complaint)
    await session.commit()
    await session.refresh(complaint)
    return ComplaintOut.model_validate(complaint)
