from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.users.models import KycStatus, pg_enum


class ResidentType(StrEnum):
    KG_RESIDENT = "kg_resident"
    FOREIGNER = "foreigner"


class KycVerification(Base):
    """Проверка личности. Файлы документов — только владельцу и модератору (R-04)."""

    __tablename__ = "kyc_verifications"
    __table_args__ = (
        Index("kyc_user_idx", "user_id", text("submitted_at DESC")),
        Index("kyc_queue_idx", "status", postgresql_where=text("status = 'pending'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    resident_type: Mapped[ResidentType | None] = mapped_column(
        pg_enum(ResidentType, "resident_type")
    )
    provider: Mapped[str] = mapped_column(Text, server_default="manual")
    provider_reference: Mapped[str | None] = mapped_column(Text)
    status: Mapped[KycStatus] = mapped_column(
        pg_enum(KycStatus, "kyc_status"), server_default=KycStatus.PENDING.value
    )
    # Имя файла в приватном хранилище (не публичный URL).
    passport_file_url: Mapped[str | None] = mapped_column(Text)
    selfie_file_url: Mapped[str | None] = mapped_column(Text)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
