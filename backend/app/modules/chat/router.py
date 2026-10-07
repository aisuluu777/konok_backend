from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BigId, get_session
from app.core.security import CurrentUser, get_current_user
from app.modules.chat import service
from app.modules.chat.schemas import (
    ConversationCreate,
    ConversationListItem,
    ConversationOut,
    MessageCreate,
    MessageOut,
)

Session = Annotated[AsyncSession, Depends(get_session)]
Me = Annotated[CurrentUser, Depends(get_current_user)]

router = APIRouter(prefix="/conversations", tags=["chat"])


@router.post("")
async def open_conversation(data: ConversationCreate, me: Me, session: Session) -> ConversationOut:
    """Возвращает единственный диалог пары, создавая его при первом обращении (R-25)."""
    conversation = await service.get_or_create_conversation(session, me.id, data.provider_id)
    return ConversationOut.model_validate(conversation)


@router.get("")
async def list_conversations(me: Me, session: Session) -> list[ConversationListItem]:
    return [
        ConversationListItem(**ConversationOut.model_validate(c).model_dump(), unread_count=n)
        for c, n in await service.list_conversations(session, me.id)
    ]


@router.get("/{conversation_id}/messages")
async def list_messages(conversation_id: BigId, me: Me, session: Session) -> list[MessageOut]:
    return await service.list_messages(session, me.id, conversation_id)


@router.post("/{conversation_id}/messages", status_code=status.HTTP_201_CREATED)
async def send_message(
    conversation_id: BigId, data: MessageCreate, me: Me, session: Session
) -> MessageOut:
    return await service.send_message(session, me.id, conversation_id, data.text)
