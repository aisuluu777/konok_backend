from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AuditLog(Base):
    """Журнал действий admin."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(50))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[int | None] = mapped_column(BigInteger)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
