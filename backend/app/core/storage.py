"""Файлы на локальном диске (MVP): <storage_dir>/public — отдаются всем, private — по правам."""

import asyncio
import math
import re
import struct
import uuid
from collections.abc import Collection, Iterator
from pathlib import Path
from typing import BinaryIO, Literal

from app.core.config import get_settings
from app.core.errors import DomainError

Visibility = Literal["public", "private"]

MiB = 1024 * 1024
CHUNK = MiB

# расширение → (MIME, лимит размера)
FORMATS: dict[str, tuple[str, int]] = {
    "jpg": ("image/jpeg", 10 * MiB),
    "png": ("image/png", 10 * MiB),
    "webp": ("image/webp", 10 * MiB),
    "mp4": ("video/mp4", 100 * MiB),
}

# Major brand в ftyp: сам «ftyp» есть и у HEIC/AVIF/M4A/MOV — пускаем только MP4.
_MP4_BRANDS = {
    b"isom",
    b"iso2",
    b"iso4",
    b"iso5",
    b"iso6",
    b"mp41",
    b"mp42",
    b"avc1",
    b"M4V ",
    b"dash",
    b"mmp4",
}

_NAME_RE = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp|mp4)$")

# Ссылки на наши публичные файлы (POST /uploads), которые можно класть в профили.
IMAGE_URL_PATTERN = r"^/files/[0-9a-f]{32}\.(jpg|png|webp)$"
VIDEO_URL_PATTERN = r"^/files/[0-9a-f]{32}\.mp4$"


class InvalidFileError(DomainError):
    code = "invalid_file"


def detect_extension(head: bytes) -> str | None:
    """Тип по сигнатуре содержимого — имени и content-type клиента не доверяем."""
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp" and head[8:12] in _MP4_BRANDS:
        return "mp4"
    return None


def _dir(visibility: Visibility) -> Path:
    return get_settings().storage_dir / visibility


IMAGES = frozenset({"jpg", "png", "webp"})


def _save(src: BinaryIO, visibility: Visibility, allowed: Collection[str]) -> str:
    head = src.read(12)
    ext = detect_extension(head)
    if ext is None or ext not in allowed:
        raise InvalidFileError("Unsupported file type")
    limit = FORMATS[ext][1]

    directory = _dir(visibility)
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    path = directory / name
    size = len(head)
    try:
        with path.open("wb") as dst:
            dst.write(head)
            while chunk := src.read(CHUNK):
                size += len(chunk)
                if size > limit:
                    raise InvalidFileError(f"File is larger than {limit // MiB} MB")
                dst.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return name


async def save(
    src: BinaryIO, visibility: Visibility, allowed: Collection[str] = FORMATS.keys()
) -> str:
    """Сохраняет поток с проверкой типа и размера; возвращает имя файла."""
    return await asyncio.to_thread(_save, src, visibility, allowed)


def resolve(name: str, visibility: Visibility) -> tuple[Path, str] | None:
    """Путь и MIME сохранённого файла; None — если имени нет или оно не по шаблону."""
    if not _NAME_RE.fullmatch(name):
        return None
    path = _dir(visibility) / name
    if not path.is_file():
        return None
    return path, FORMATS[name.rsplit(".", 1)[1]][0]


def delete(name: str, visibility: Visibility) -> None:
    if _NAME_RE.fullmatch(name):
        (_dir(visibility) / name).unlink(missing_ok=True)


_MAX_BOXES = 1000  # в настоящем MP4 на уровне — единицы/десятки боксов


def _boxes(f: BinaryIO, end: int) -> Iterator[tuple[bytes, int, int]]:
    """MP4 (ISO BMFF) боксы в [f.tell(), end): (тип, начало данных, конец бокса)."""
    # Лимит числа боксов: файл из миллионов крошечных боксов иначе грузит CPU надолго.
    for _ in range(_MAX_BOXES):
        if f.tell() + 8 > end:
            return
        start = f.tell()
        size, kind = struct.unpack(">I4s", f.read(8))
        header = 8
        if size == 1:
            (size,) = struct.unpack(">Q", f.read(8))
            header = 16
        elif size == 0:
            size = end - start
        if size < header or start + size > end:
            return
        yield kind, start + header, start + size
        f.seek(start + size)


def _mp4_duration(path: Path) -> float | None:
    with path.open("rb") as f:
        f.seek(0, 2)
        file_end = f.tell()
        f.seek(0)
        for kind, data_start, box_end in _boxes(f, file_end):
            if kind != b"moov":
                continue
            f.seek(data_start)
            for inner, inner_start, _ in _boxes(f, box_end):
                if inner != b"mvhd":
                    continue
                f.seek(inner_start)
                version = f.read(1)[0]
                f.read(3)  # flags
                if version == 1:
                    _, _, timescale, duration = struct.unpack(">QQIQ", f.read(28))
                else:
                    _, _, timescale, duration = struct.unpack(">IIII", f.read(16))
                return duration / timescale if timescale else None
    return None


async def video_duration_sec(name: str) -> int | None:
    """Длительность сохранённого публичного MP4 в секундах (с округлением вверх) по mvhd."""
    found = resolve(name, "public")
    if found is None or not name.endswith(".mp4"):
        return None
    try:
        seconds = await asyncio.to_thread(_mp4_duration, found[0])
    except (OSError, struct.error, IndexError):
        return None
    return math.ceil(seconds) if seconds is not None else None
