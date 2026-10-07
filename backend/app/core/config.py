from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки окружения (из переменных окружения или .env).

    Бизнес-параметры (TTL токенов и OTP, сроки hold и т.п.) — в таблице settings, не здесь.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str
    secret_key: SecretStr
    # Папка для загруженных файлов (локальный диск в MVP): public/ и private/ внутри.
    storage_dir: Path = Path("storage")


@lru_cache
def get_settings() -> Settings:
    return Settings()
