from collections.abc import Sequence

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError, NotFoundError, PermissionDeniedError
from app.modules.chat.models import Conversation, Message
from app.modules.chat.schemas import MessageOut
from app.modules.hosts.models import HostProfile
from app.modules.users.models import User, UserStatus
from app.modules.vehicles.models import Vehicle


def _out(message: Message) -> MessageOut:
    return MessageOut(
        id=message.id,
        conversation_id=message.conversation_id,
        sender_id=message.sender_id,
        text=message.original_text,
        read_at=message.read_at,
        created_at=message.created_at,
    )


async def get_or_create_conversation(
    session: AsyncSession, guest_id: int, provider_id: int
) -> Conversation:
    """R-25: единственный диалог пары; бронь не нужна. Собеседник — хозяин или владелец машины."""
    if provider_id == guest_id:
        raise DomainError("Cannot start a conversation with yourself")
    is_provider = exists().where(HostProfile.user_id == provider_id) | exists().where(
        Vehicle.owner_id == provider_id
    )
    provider_ok = await session.scalar(
        select(User.id).where(
            User.id == provider_id,
            User.status == UserStatus.ACTIVE,
            select(is_provider).scalar_subquery(),
        )
    )
    if provider_ok is None:
        raise NotFoundError("Provider not found")
    # Параллельное создание той же пары не падает на UNIQUE.
    await session.execute(
        insert(Conversation)
        .values(guest_id=guest_id, provider_id=provider_id)
        .on_conflict_do_nothing(constraint="conversations_pair_uq")
    )
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.guest_id == guest_id, Conversation.provider_id == provider_id
        )
    )
    await session.commit()
    assert conversation is not None
    return conversation


async def list_conversations(
    session: AsyncSession, user_id: int
) -> Sequence[tuple[Conversation, int]]:
    """Диалоги пользователя (как гостя или как собеседника) с числом непрочитанных входящих."""
    unread = (
        select(func.count())
        .where(
            Message.conversation_id == Conversation.id,
            Message.sender_id != user_id,
            Message.read_at.is_(None),
        )
        .scalar_subquery()
    )
    rows = await session.execute(
        select(Conversation, unread)
        .where(or_(Conversation.guest_id == user_id, Conversation.provider_id == user_id))
        .order_by(Conversation.last_message_at.desc().nulls_last(), Conversation.id.desc())
    )
    return [(c, n) for c, n in rows.all()]


async def _participant_conversation(
    session: AsyncSession, user_id: int, conversation_id: int
) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise NotFoundError("Conversation not found")
    if user_id not in (conversation.guest_id, conversation.provider_id):
        raise PermissionDeniedError("Not a participant of this conversation")
    return conversation


async def list_messages(
    session: AsyncSession, user_id: int, conversation_id: int
) -> list[MessageOut]:
    """Сообщения по порядку; входящие помечаются прочитанными получателем."""
    await _participant_conversation(session, user_id, conversation_id)
    await session.execute(
        update(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.sender_id != user_id,
            Message.read_at.is_(None),
        )
        .values(read_at=func.now())
    )
    messages = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    )
    result = [_out(m) for m in messages.all()]
    await session.commit()
    return result


async def send_message(
    session: AsyncSession, user_id: int, conversation_id: int, text: str
) -> MessageOut:
    conversation = await _participant_conversation(session, user_id, conversation_id)
    message = Message(conversation_id=conversation_id, sender_id=user_id, original_text=text)
    session.add(message)
    await session.flush()
    await session.refresh(message)
    conversation.last_message_at = message.created_at
    await session.commit()
    return _out(message)
