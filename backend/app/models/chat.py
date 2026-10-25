from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.users import User


class Conversation(Base):
    """Чат 1-на-1: гость и хозяин / владелец авто."""

    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("guest_id", "owner_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    guest_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    guest: Mapped[User] = relationship(foreign_keys=[guest_id], lazy="selectin")
    owner: Mapped[User] = relationship(foreign_keys=[owner_id], lazy="selectin")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text)  # оригинал
    # Перевод Azure; при ошибке перевода — пусто, показывается оригинал.
    text_ky: Mapped[str | None] = mapped_column(Text)
    text_ru: Mapped[str | None] = mapped_column(Text)
    text_en: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Complaint(Base):
    """Жалоба на пользователя или на его сообщение."""

    __tablename__ = "complaints"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    target_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    text: Mapped[str] = mapped_column(Text)
    is_resolved: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
