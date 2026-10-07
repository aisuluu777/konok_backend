"""Загрузка и раздача файлов: POST /uploads, GET /files/{name} (Step 4 plan).

Инфраструктура для фото и видео объявлений (R-07). Тип файла определяется по содержимому
(сигнатуре), имя и content-type клиента игнорируются.
"""

import os
import re
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.admin.helpers import assert_error
from tests.factories import auth, create_user, login

MIB = 1024 * 1024
PHOTO_LIMIT = 10 * MIB

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x01" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x02" * 64
WEBP = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBP" + b"VP8 " + b"\x03" * 64
MP4 = b"\x00\x00\x00\x18" + b"ftyp" + b"mp42" + b"\x00\x00\x00\x00" + b"\x04" * 64

URL_RE = re.compile(r"^/files/([0-9a-f]{32})\.(jpg|png|webp|mp4)$")


def public_dir() -> Path:
    return Path(os.environ["STORAGE_DIR"]) / "public"


def stored_files() -> set[str]:
    directory = public_dir()
    if not directory.exists():
        return set()
    return {p.name for p in directory.iterdir()}


async def user_headers(db_session: AsyncSession, client: AsyncClient) -> dict[str, str]:
    user = await create_user(db_session)
    tokens = await login(client, user.email)
    return auth(tokens["access_token"])


async def upload(
    client: AsyncClient,
    headers: dict[str, str],
    content: bytes,
    filename: str = "upload.bin",
    content_type: str = "application/octet-stream",
) -> str:
    """Загружает файл, ожидает 201 и возвращает url из ответа."""
    response = await client.post(
        "/uploads", headers=headers, files={"file": (filename, content, content_type)}
    )
    assert response.status_code == 201, response.text
    url: str = response.json()["url"]
    return url


# --- POST /uploads: успешные загрузки ---


@pytest.mark.parametrize(
    ("content", "ext", "media_type"),
    [
        (JPEG, "jpg", "image/jpeg"),
        (PNG, "png", "image/png"),
        (WEBP, "webp", "image/webp"),
        (MP4, "mp4", "video/mp4"),
    ],
    ids=["jpeg", "png", "webp", "mp4"],
)
async def test_upload_and_download_roundtrip(
    client: AsyncClient,
    db_session: AsyncSession,
    content: bytes,
    ext: str,
    media_type: str,
) -> None:
    """Step 4 plan (R-07 для mp4): POST /uploads → 201 {"url": "/files/<32 hex>.<ext>"},
    GET по url без токена отдаёт те же байты с Content-Type по типу."""
    headers = await user_headers(db_session, client)

    response = await client.post(
        "/uploads", headers=headers, files={"file": ("upload.bin", content, "text/plain")}
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"url"}
    match = URL_RE.match(body["url"])
    assert match, body
    assert match.group(2) == ext

    got = await client.get(body["url"])
    assert got.status_code == 200, got.text
    assert got.content == content
    assert got.headers["content-type"].split(";")[0].strip() == media_type


async def test_uploaded_file_is_stored_in_public_dir(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 4 plan: загруженный файл лежит в <STORAGE_DIR>/public/<name> с тем же содержимым."""
    headers = await user_headers(db_session, client)

    url = await upload(client, headers, PNG)

    name = url.removeprefix("/files/")
    path = public_dir() / name
    assert path.is_file()
    assert path.read_bytes() == PNG


@pytest.mark.parametrize(
    ("content", "filename", "content_type", "ext"),
    [
        (PNG, "photo.jpg", "image/jpeg", "png"),
        (JPEG, "clip.mp4", "video/mp4", "jpg"),
        (MP4, "image.png", "image/png", "mp4"),
        (WEBP, "noext", "application/octet-stream", "webp"),
    ],
    ids=["png-as-jpg", "jpeg-as-mp4", "mp4-as-png", "webp-noext"],
)
async def test_extension_comes_from_content_not_client(
    client: AsyncClient,
    db_session: AsyncSession,
    content: bytes,
    filename: str,
    content_type: str,
    ext: str,
) -> None:
    """Step 4 plan: расширение определяется по сигнатуре; имя и content-type клиента
    игнорируются."""
    headers = await user_headers(db_session, client)

    url = await upload(client, headers, content, filename, content_type)

    match = URL_RE.match(url)
    assert match, url
    assert match.group(2) == ext


async def test_same_content_twice_gives_different_urls(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 4 plan: одинаковое содержимое дважды → два разных URL, оба доступны."""
    headers = await user_headers(db_session, client)

    first = await upload(client, headers, JPEG)
    second = await upload(client, headers, JPEG)

    assert first != second
    for url in (first, second):
        got = await client.get(url)
        assert got.status_code == 200, got.text
        assert got.content == JPEG


async def test_photo_exactly_at_limit_accepted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 4 plan (граница): фото ровно 10 MiB → 201."""
    headers = await user_headers(db_session, client)
    content = JPEG + b"\x00" * (PHOTO_LIMIT - len(JPEG))
    assert len(content) == PHOTO_LIMIT

    url = await upload(client, headers, content)

    assert URL_RE.match(url), url


# --- POST /uploads: отклонённые файлы ---


@pytest.mark.parametrize(
    ("content", "filename", "content_type"),
    [
        (b"just some plain text", "notes.txt", "text/plain"),
        (b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj", "doc.pdf", "application/pdf"),
        (b"definitely not a jpeg", "photo.jpg", "image/jpeg"),
        (b"", "empty.jpg", "image/jpeg"),
        (b"\xff\xd8", "short.jpg", "image/jpeg"),
        (b"RIFF\x24\x00\x00\x00WAVE", "sound.webp", "image/webp"),
        (b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00", "photo.mp4", "video/mp4"),
        (b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00", "song.mp4", "video/mp4"),
        (b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00", "clip.mp4", "video/mp4"),
    ],
    ids=[
        "text",
        "pdf",
        "text-as-jpeg",
        "empty",
        "truncated-jpeg-signature",
        "riff-not-webp",
        "heic-not-mp4",
        "m4a-not-mp4",
        "mov-not-mp4",
    ],
)
async def test_invalid_content_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
    content: bytes,
    filename: str,
    content_type: str,
) -> None:
    """Step 4 plan: содержимое не JPEG/PNG/WebP/MP4 (в т.ч. пустое) → 400 invalid_file,
    на диске ничего не остаётся."""
    headers = await user_headers(db_session, client)
    before = stored_files()

    response = await client.post(
        "/uploads", headers=headers, files={"file": (filename, content, content_type)}
    )

    assert_error(response, 400, "invalid_file")
    assert stored_files() == before


@pytest.mark.parametrize(
    "signature",
    [JPEG, PNG, WEBP],
    ids=["jpeg", "png", "webp"],
)
async def test_photo_over_limit_rejected(
    client: AsyncClient, db_session: AsyncSession, signature: bytes
) -> None:
    """Step 4 plan (граница): фото на 1 байт больше 10 MiB → 400 invalid_file,
    файл не остаётся на диске."""
    headers = await user_headers(db_session, client)
    content = signature + b"\x00" * (PHOTO_LIMIT + 1 - len(signature))
    assert len(content) == PHOTO_LIMIT + 1
    before = stored_files()

    response = await client.post(
        "/uploads", headers=headers, files={"file": ("big.bin", content, "image/jpeg")}
    )

    assert_error(response, 400, "invalid_file")
    assert stored_files() == before


async def test_upload_requires_auth(client: AsyncClient) -> None:
    """Step 4 plan: без токена → 401 unauthorized, файл не сохраняется."""
    before = stored_files()

    response = await client.post("/uploads", files={"file": ("photo.jpg", JPEG, "image/jpeg")})

    assert_error(response, 401, "unauthorized")
    assert stored_files() == before


async def test_upload_without_file_field(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 4 plan: multipart без поля file → 422 validation_error."""
    headers = await user_headers(db_session, client)

    response = await client.post(
        "/uploads", headers=headers, files={"other": ("photo.jpg", JPEG, "image/jpeg")}
    )

    assert_error(response, 422, "validation_error")


async def test_upload_without_body(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 4 plan: запрос без тела → 422 validation_error."""
    headers = await user_headers(db_session, client)

    response = await client.post("/uploads", headers=headers)

    assert_error(response, 422, "validation_error")


# --- GET /files/{name} ---


async def test_download_unknown_name_not_found(client: AsyncClient) -> None:
    """Step 4 plan: корректное по шаблону, но несуществующее имя → 404 not_found."""
    response = await client.get(f"/files/{'0' * 32}.jpg")

    assert_error(response, 404, "not_found")


@pytest.mark.parametrize(
    "name",
    [
        "abc.txt",
        "..%2F..%2Fetc%2Fpasswd",
        f"{'a' * 32}.exe",
        f"{'a' * 31}.jpg",
        f"{'a' * 33}.jpg",
        f"{'A' * 32}.jpg",
        f"{'g' * 32}.png",
        f"{'a' * 32}",
        f"{'a' * 32}.jpg.exe",
    ],
)
async def test_download_bad_name_not_found(client: AsyncClient, name: str) -> None:
    """Step 4 plan: имя не по шаблону <32 hex>.(jpg|png|webp|mp4) → 404 not_found."""
    response = await client.get(f"/files/{name}")

    assert_error(response, 404, "not_found")


async def test_download_bad_name_not_served_even_if_on_disk(client: AsyncClient) -> None:
    """Step 4 plan: файл с недопустимым именем, лежащий в public/, не отдаётся → 404 not_found."""
    directory = public_dir()
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{'b' * 32}.exe"
    (directory / name).write_bytes(JPEG)
    try:
        response = await client.get(f"/files/{name}")
    finally:
        (directory / name).unlink()

    assert_error(response, 404, "not_found")


async def test_download_is_public(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 4 plan: GET /files/{name} доступен без токена и другим пользователям."""
    headers = await user_headers(db_session, client)
    url = await upload(client, headers, WEBP)

    anonymous = await client.get(url)
    other = await client.get(url, headers=await user_headers(db_session, client))

    assert anonymous.status_code == 200, anonymous.text
    assert other.status_code == 200, other.text
    assert anonymous.content == other.content == WEBP
