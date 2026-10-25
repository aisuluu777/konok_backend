from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Setting(Base):
    """Глобальные настройки платформы (комиссия, время заезда и выезда); правит admin."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
