from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Enum, Index, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class UserRole(StrEnum):
    USER = "user"
    MODERATOR = "moderator"
    ADMIN = "admin"


class UserStatus(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"


class KycStatus(StrEnum):
    NOT_STARTED = "not_started"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    RESUBMISSION = "resubmission"


def pg_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    # Типы создаёт миграция; в БД хранятся значения enum, а не имена членов.
    return Enum(
        enum_cls,
        name=name,
        create_type=False,
        values_callable=lambda cls: [member.value for member in cls],
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("users_email_uq", text("lower(email)"), unique=True),
        Index("users_phone_uq", "phone", unique=True),
        CheckConstraint("checkin_pin ~ '^[0-9]{6}$'"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[UserRole] = mapped_column(
        pg_enum(UserRole, "user_role"), server_default=UserRole.USER.value
    )
    status: Mapped[UserStatus] = mapped_column(
        pg_enum(UserStatus, "user_status"), server_default=UserStatus.ACTIVE.value
    )
    first_name: Mapped[str] = mapped_column(Text)
    last_name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text)
    phone: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terms_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kyc_status: Mapped[KycStatus] = mapped_column(
        pg_enum(KycStatus, "kyc_status"), server_default=KycStatus.NOT_STARTED.value
    )
    # R-23: PIN заселения, выдаётся при одобрении KYC; открыто — решение MVP, не окончательное.
    checkin_pin: Mapped[str | None] = mapped_column(Text)
    languages: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
