"""Файлы на локальном диске: фото — в public/ (отдаются по /media), документы — в private/."""

import uuid
from pathlib import Path

from fastapi import UploadFile

from app.config import get_settings
from app.errors import AppError, bad_request

MAX_SIZE = 10 * 1024 * 1024  # 10 МБ
IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
DOCUMENT_TYPES = {**IMAGE_TYPES, "application/pdf": ".pdf"}


async def _save(file: UploadFile, folder: str, allowed: dict[str, str]) -> str:
    extension = allowed.get(file.content_type or "")
    if extension is None:
        raise bad_request(f"Недопустимый тип файла. Разрешены: {', '.join(allowed)}")
    data = await file.read(MAX_SIZE + 1)
    if len(data) > MAX_SIZE:
        raise AppError(413, "Файл больше 10 МБ")
    name = f"{uuid.uuid4().hex}{extension}"
    directory = get_settings().storage_dir / folder
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(data)
    return name


async def save_photo(file: UploadFile) -> str:
    """Возвращает публичный URL фото."""
    return f"/media/{await _save(file, 'public', IMAGE_TYPES)}"


async def save_document(file: UploadFile) -> str:
    """Возвращает имя файла в private/ (наружу не отдаётся, смотрит только admin)."""
    return await _save(file, "private", DOCUMENT_TYPES)


def private_path(name: str) -> Path | None:
    path = get_settings().storage_dir / "private" / Path(name).name
    return path if path.is_file() else None


def delete_photo(url: str) -> None:
    path = get_settings().storage_dir / "public" / Path(url).name
    path.unlink(missing_ok=True)
