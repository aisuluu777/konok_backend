from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки окружения: из переменных окружения или файла .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str
    access_token_minutes: int = 30
    refresh_token_days: int = 30
    otp_minutes: int = 10

    # Домены фронтенда для CORS через запятую, например https://konok.kg,https://www.konok.kg
    cors_origins: str = ""

    # Платежи: сейчас есть только mock.
    payment_provider: str = "mock"

    # Почта для OTP. Пустой SMTP_HOST — код пишется в лог (для разработки).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@konok.kg"

    # Azure AI Translator (перевод сообщений чата). Пустой ключ — перевода нет.
    azure_translator_key: str = ""
    azure_translator_region: str = ""
    azure_translator_endpoint: str = "https://api.cognitive.microsofttranslator.com"

    # Web push (VAPID). Ключи: python scripts/generate_vapid_keys.py. Пустые — push не шлётся.
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:admin@konok.kg"

    # Загруженные файлы: public/ — фото (отдаются по /media), private/ — документы KYC.
    storage_dir: Path = Path("storage")


@lru_cache
def get_settings() -> Settings:
    return Settings()
