from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, str_enum


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[UserRole] = mapped_column(str_enum(UserRole), default=UserRole.USER)
    is_email_confirmed: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)  # false — заблокирован admin

    # KYC: документ (ID) загружает пользователь, is_verified ставит и снимает admin.
    id_document_url: Mapped[str | None] = mapped_column(Text)
    is_verified: Mapped[bool] = mapped_column(default=False)
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )  # «на платформе с»
    driver_license_url: Mapped[str | None] = mapped_column(Text)
    is_driver_license_verified: Mapped[bool] = mapped_column(default=False)

    # PIN заселения: хранится открыто, гость видит его в личном кабинете.
    checkin_pin: Mapped[str] = mapped_column(String(6))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __str__(self) -> str:
        return self.email
