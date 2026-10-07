from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

IN_APP = "in_app"


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("notifications_user_idx", "user_id", text("created_at DESC")),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(Text, server_default=IN_APP)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
