from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Conversation(Base):
    """Один диалог на пару «гость — хозяин или владелец машины» (R-25)."""

    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("guest_id <> provider_id", name="conversations_not_self_chk"),
        UniqueConstraint("guest_id", "provider_id", name="conversations_pair_uq"),
        Index("conversations_provider_idx", "provider_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    guest_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    provider_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    """Оригинал хранится всегда (R-25); поля перевода пока не используются."""

    __tablename__ = "messages"
    __table_args__ = (
        Index("messages_conversation_idx", "conversation_id", "created_at"),
        Index("messages_unread_idx", "conversation_id", postgresql_where=text("read_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversations.id", ondelete="CASCADE")
    )
    sender_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    original_text: Mapped[str] = mapped_column(Text)
    original_language: Mapped[str | None] = mapped_column(String(8))
    translated_text: Mapped[str | None] = mapped_column(Text)
    translated_language: Mapped[str | None] = mapped_column(String(8))
    is_flagged: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
