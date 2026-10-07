"""KYC: подача документов, приватная раздача, решение модератора (Step 5 plan).

R-04 — документы видят только владелец и moderator/admin, в публичных ответах их нет;
R-28 — решение по KYC пишется в аудит; R-39 — уведомление о решении; R-03 — users.kyc_status
копирует статус проверки (от него зависит видимость хозяина).
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.modules.admin.models import Setting
from app.modules.kyc.models import KycVerification, ResidentType
from app.modules.notifications.models import Notification
from app.modules.users.models import KycStatus, User, UserRole
from tests.admin.helpers import assert_error
from tests.factories import auth, create_user, login

MIB = 1024 * 1024
PHOTO_LIMIT = 10 * MIB

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x01" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x02" * 64
WEBP = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBP" + b"VP8 " + b"\x03" * 64
MP4 = b"\x00\x00\x00\x18" + b"ftyp" + b"mp42" + b"\x00\x00\x00\x00" + b"\x04" * 64

KYC_FIELDS = {
    "id",
    "user_id",
    "status",
    "resident_type",
    "rejection_reason",
    "submitted_at",
    "checked_at",
}
DOC_KEYS = {"passport_file_url", "selfie_file_url"}
MODERATOR_ROLES = [UserRole.MODERATOR, UserRole.ADMIN]


def unique(signature: bytes) -> bytes:
    """Фото с уникальным хвостом — чтобы найти именно этот файл на диске."""
    return signature + os.urandom(16)


def storage_dir() -> Path:
    return Path(os.environ["STORAGE_DIR"])


def files_with_content(directory: Path, content: bytes) -> list[Path]:
    if not directory.exists():
        return []
    return [p for p in directory.rglob("*") if p.is_file() and p.read_bytes() == content]


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def user_with_headers(
    db_session: AsyncSession, client: AsyncClient, role: UserRole = UserRole.USER
) -> tuple[User, dict[str, str]]:
    user = await create_user(db_session, role=role)
    tokens = await login(client, user.email)
    return user, auth(tokens["access_token"])


async def post_kyc(
    client: AsyncClient,
    headers: dict[str, str] | None,
    passport: bytes | None = JPEG,
    selfie: bytes | None = PNG,
    resident_type: str | None = None,
) -> Response:
    files: dict[str, tuple[str, bytes, str]] = {}
    if passport is not None:
        files["passport"] = ("passport.jpg", passport, "image/jpeg")
    if selfie is not None:
        files["selfie"] = ("selfie.png", selfie, "image/png")
    data = {"resident_type": resident_type} if resident_type is not None else None
    return await client.post("/kyc", headers=headers, files=files or None, data=data)


async def submit(
    client: AsyncClient,
    headers: dict[str, str],
    passport: bytes = JPEG,
    selfie: bytes = PNG,
    resident_type: str | None = None,
) -> dict[str, Any]:
    """Подаёт заявку, ожидает 201 и возвращает KycOut."""
    response = await post_kyc(client, headers, passport, selfie, resident_type)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def kyc_row(db_session: AsyncSession, kyc_id: int) -> KycVerification:
    row = await db_session.scalar(
        select(KycVerification)
        .where(KycVerification.id == kyc_id)
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


async def kyc_count(db_session: AsyncSession, user_id: int | None = None) -> int:
    query = select(func.count()).select_from(KycVerification)
    if user_id is not None:
        query = query.where(KycVerification.user_id == user_id)
    return int(await db_session.scalar(query) or 0)


async def user_kyc_status(db_session: AsyncSession, user_id: int) -> KycStatus:
    value = await db_session.scalar(
        select(User.kyc_status).where(User.id == user_id).execution_options(populate_existing=True)
    )
    assert value is not None
    return KycStatus(value)


async def set_kyc_times(
    db_session: AsyncSession,
    kyc_id: int,
    *,
    submitted_at: datetime | None = None,
    checked_at: datetime | None = None,
) -> None:
    values: dict[str, datetime] = {}
    if submitted_at is not None:
        values["submitted_at"] = submitted_at
    if checked_at is not None:
        values["checked_at"] = checked_at
    await db_session.execute(
        update(KycVerification).where(KycVerification.id == kyc_id).values(**values)
    )
    await db_session.commit()


async def set_resubmit_hours(db_session: AsyncSession, hours: str) -> None:
    current = await db_session.scalar(
        select(Setting.value).where(Setting.key == "kyc_resubmit_hours")
    )
    assert current is not None, "сид настройки kyc_resubmit_hours отсутствует"
    await db_session.execute(
        update(Setting).where(Setting.key == "kyc_resubmit_hours").values(value=hours)
    )
    await db_session.commit()


async def kyc_audit(db_session: AsyncSession, kyc_id: int) -> list[AuditLog]:
    result = await db_session.scalars(
        select(AuditLog)
        .where(AuditLog.entity_type == "kyc_verification", AuditLog.entity_id == kyc_id)
        .order_by(AuditLog.id)
    )
    return list(result)


async def audit_count(db_session: AsyncSession) -> int:
    return int(await db_session.scalar(select(func.count()).select_from(AuditLog)) or 0)


async def user_notifications(db_session: AsyncSession, user_id: int) -> list[Notification]:
    result = await db_session.scalars(
        select(Notification).where(Notification.user_id == user_id).order_by(Notification.id)
    )
    return list(result)


async def notification_count(db_session: AsyncSession) -> int:
    return int(await db_session.scalar(select(func.count()).select_from(Notification)) or 0)


def assert_kyc_out(item: dict[str, Any]) -> None:
    """R-04: KycOut — ровно описанные поля, без ключей и путей документов."""
    assert set(item) == KYC_FIELDS, item
    assert not DOC_KEYS & set(item)


def assert_no_doc_paths(response: Response, row: KycVerification) -> None:
    """R-04: в теле ответа нет ни путей, ни имён файлов документов."""
    text = response.text
    for value in (row.passport_file_url, row.selfie_file_url):
        assert value
        assert value not in text
        assert Path(value).name not in text
    for key in DOC_KEYS:
        assert key not in text


async def rejected_user(
    client: AsyncClient, db_session: AsyncSession, mod_headers: dict[str, str]
) -> tuple[User, dict[str, str], dict[str, Any]]:
    """Пользователь с отклонённой заявкой (checked_at = сейчас)."""
    user, headers = await user_with_headers(db_session, client)
    kyc = await submit(client, headers)
    response = await client.post(
        f"/kyc/{kyc['id']}/reject", headers=mod_headers, json={"reason": "Нечитаемо"}
    )
    assert response.status_code == 200, response.text
    return user, headers, kyc


# --- без токена ---


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/kyc"),
        ("GET", "/kyc/my"),
        ("GET", "/kyc"),
        ("GET", "/kyc/1/passport"),
        ("GET", "/kyc/1/selfie"),
        ("POST", "/kyc/1/approve"),
        ("POST", "/kyc/1/reject"),
    ],
)
async def test_kyc_endpoints_require_auth(client: AsyncClient, method: str, path: str) -> None:
    """R-04: все KYC-эндпоинты только с токеном — без него 401 unauthorized."""
    response = await client.request(method, path)

    assert_error(response, 401, "unauthorized")


async def test_documents_without_token_unauthorized_for_existing_kyc(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-04: документы существующей заявки без токена не отдаются → 401 unauthorized."""
    _, headers = await user_with_headers(db_session, client)
    kyc = await submit(client, headers)

    for doc in ("passport", "selfie"):
        response = await client.get(f"/kyc/{kyc['id']}/{doc}")
        assert_error(response, 401, "unauthorized")


# --- POST /kyc ---


async def test_submit_creates_pending_kyc(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 5 plan, R-03: POST /kyc → 201 KycOut pending; users.kyc_status = pending."""
    user, headers = await user_with_headers(db_session, client)

    response = await post_kyc(client, headers)

    assert response.status_code == 201, response.text
    body = response.json()
    assert_kyc_out(body)
    assert body["user_id"] == user.id
    assert body["status"] == "pending"
    assert body["resident_type"] is None
    assert body["rejection_reason"] is None
    assert body["checked_at"] is None
    assert parse_ts(body["submitted_at"])

    row = await kyc_row(db_session, body["id"])
    assert row.user_id == user.id
    assert row.status == KycStatus.PENDING
    assert row.reviewed_by is None
    assert row.passport_file_url
    assert row.selfie_file_url
    assert_no_doc_paths(response, row)
    assert await user_kyc_status(db_session, user.id) == KycStatus.PENDING


@pytest.mark.parametrize("resident_type", ["kg_resident", "foreigner"])
async def test_submit_with_resident_type(
    client: AsyncClient, db_session: AsyncSession, resident_type: str
) -> None:
    """Step 5 plan: опциональный resident_type сохраняется и возвращается."""
    _, headers = await user_with_headers(db_session, client)

    body = await submit(client, headers, resident_type=resident_type)

    assert body["resident_type"] == resident_type
    row = await kyc_row(db_session, body["id"])
    assert row.resident_type == ResidentType(resident_type)


async def test_submit_invalid_resident_type(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 5 plan: resident_type вне enum → 422 validation_error, заявки нет."""
    user, headers = await user_with_headers(db_session, client)

    response = await post_kyc(client, headers, resident_type="alien")

    assert_error(response, 422, "validation_error")
    assert await kyc_count(db_session) == 0
    assert await user_kyc_status(db_session, user.id) == KycStatus.NOT_STARTED


@pytest.mark.parametrize(
    ("passport", "selfie"),
    [(None, PNG), (JPEG, None), (None, None)],
    ids=["no-passport", "no-selfie", "no-files"],
)
async def test_submit_requires_both_files(
    client: AsyncClient, db_session: AsyncSession, passport: bytes | None, selfie: bytes | None
) -> None:
    """Step 5 plan: без passport или selfie → 422 validation_error, заявки нет."""
    user, headers = await user_with_headers(db_session, client)

    response = await post_kyc(client, headers, passport, selfie)

    assert_error(response, 422, "validation_error")
    assert await kyc_count(db_session) == 0
    assert await user_kyc_status(db_session, user.id) == KycStatus.NOT_STARTED


@pytest.mark.parametrize(
    ("passport", "selfie"),
    [
        (MP4, PNG),
        (JPEG, MP4),
        (b"just some plain text", PNG),
        (JPEG, b"just some plain text"),
        (b"", PNG),
        (JPEG + b"\x00" * (PHOTO_LIMIT + 1 - len(JPEG)), PNG),
        (JPEG, PNG + b"\x00" * (PHOTO_LIMIT + 1 - len(PNG))),
    ],
    ids=[
        "passport-mp4",
        "selfie-mp4",
        "passport-text",
        "selfie-text",
        "passport-empty",
        "passport-over-limit",
        "selfie-over-limit",
    ],
)
async def test_submit_invalid_document_rejected(
    client: AsyncClient, db_session: AsyncSession, passport: bytes, selfie: bytes
) -> None:
    """Step 5 plan: документ не фото (MP4, текст, пусто) или больше 10 MiB → 400 invalid_file;
    заявка не создаётся, kyc_status не меняется."""
    user, headers = await user_with_headers(db_session, client)

    response = await post_kyc(client, headers, passport, selfie)

    assert_error(response, 400, "invalid_file")
    assert await kyc_count(db_session) == 0
    assert await user_kyc_status(db_session, user.id) == KycStatus.NOT_STARTED


async def test_submit_photo_exactly_at_limit_accepted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 5 plan (граница): документ ровно 10 MiB → 201."""
    _, headers = await user_with_headers(db_session, client)
    passport = JPEG + b"\x00" * (PHOTO_LIMIT - len(JPEG))

    body = await submit(client, headers, passport=passport)

    assert body["status"] == "pending"


async def test_documents_stored_privately(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-04: документы лежат в <STORAGE_DIR>/private/, не в public/,
    и через публичный GET /files/<имя> не отдаются."""
    _, headers = await user_with_headers(db_session, client)
    passport, selfie = unique(JPEG), unique(PNG)

    body = await submit(client, headers, passport=passport, selfie=selfie)

    for content in (passport, selfie):
        assert len(files_with_content(storage_dir() / "private", content)) == 1
        assert files_with_content(storage_dir() / "public", content) == []

    row = await kyc_row(db_session, body["id"])
    for value in (row.passport_file_url, row.selfie_file_url):
        assert value
        response = await client.get(f"/files/{Path(value).name}", headers=headers)
        assert_error(response, 404, "not_found")


@pytest.mark.parametrize("status", [KycStatus.PENDING, KycStatus.APPROVED])
async def test_submit_conflict_when_pending_or_approved(
    client: AsyncClient, db_session: AsyncSession, status: KycStatus
) -> None:
    """Step 5 plan: kyc_status pending или approved → 409 conflict, новой заявки нет."""
    _, mod_headers = await user_with_headers(db_session, client, UserRole.MODERATOR)
    user, headers = await user_with_headers(db_session, client)
    kyc = await submit(client, headers)
    if status == KycStatus.APPROVED:
        approved = await client.post(f"/kyc/{kyc['id']}/approve", headers=mod_headers)
        assert approved.status_code == 200, approved.text

    response = await post_kyc(client, headers)

    assert_error(response, 409, "conflict")
    assert await kyc_count(db_session, user.id) == 1
    assert await user_kyc_status(db_session, user.id) == status


async def test_resubmit_after_reject_too_early(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Решение 2026-10-05: после отказа повторная подача раньше kyc_resubmit_hours (сид 1 ч)
    → 429 too_many_requests; новой заявки нет, kyc_status остаётся rejected."""
    _, mod_headers = await user_with_headers(db_session, client, UserRole.MODERATOR)
    user, headers, _ = await rejected_user(client, db_session, mod_headers)

    response = await post_kyc(client, headers)

    assert_error(response, 429, "too_many_requests")
    assert await kyc_count(db_session, user.id) == 1
    assert await user_kyc_status(db_session, user.id) == KycStatus.REJECTED


async def test_resubmit_after_reject_when_time_passed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Решение 2026-10-05: прошло больше kyc_resubmit_hours с отказа → 201, новая строка pending,
    старая остаётся rejected, kyc_status = pending."""
    _, mod_headers = await user_with_headers(db_session, client, UserRole.MODERATOR)
    user, headers, old = await rejected_user(client, db_session, mod_headers)
    await set_kyc_times(db_session, old["id"], checked_at=datetime.now(UTC) - timedelta(hours=2))

    body = await submit(client, headers, resident_type="foreigner")

    assert body["id"] != old["id"]
    assert body["status"] == "pending"
    assert body["rejection_reason"] is None
    assert body["checked_at"] is None
    assert (await kyc_row(db_session, old["id"])).status == KycStatus.REJECTED
    assert await kyc_count(db_session, user.id) == 2
    assert await user_kyc_status(db_session, user.id) == KycStatus.PENDING


async def test_resubmit_respects_changed_setting(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Решение 2026-10-05: окно берётся из settings.kyc_resubmit_hours — при 48 ч отказ
    двухчасовой давности ещё рано → 429; при 1 ч → 201."""
    _, mod_headers = await user_with_headers(db_session, client, UserRole.MODERATOR)
    user, headers, old = await rejected_user(client, db_session, mod_headers)
    await set_kyc_times(db_session, old["id"], checked_at=datetime.now(UTC) - timedelta(hours=2))
    await set_resubmit_hours(db_session, "48")

    too_early = await post_kyc(client, headers)

    assert_error(too_early, 429, "too_many_requests")
    assert await kyc_count(db_session, user.id) == 1

    await set_resubmit_hours(db_session, "1")

    body = await submit(client, headers)
    assert body["status"] == "pending"


async def test_resubmit_window_counts_from_latest_rejection(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Решение 2026-10-05: окно отсчитывается от checked_at последней заявки — старый
    давний отказ не открывает подачу, если последний отказ свежий."""
    _, mod_headers = await user_with_headers(db_session, client, UserRole.MODERATOR)
    user, headers, first = await rejected_user(client, db_session, mod_headers)
    long_ago = datetime.now(UTC) - timedelta(days=10)
    await set_kyc_times(db_session, first["id"], submitted_at=long_ago, checked_at=long_ago)
    second = await submit(client, headers)
    rejected = await client.post(
        f"/kyc/{second['id']}/reject", headers=mod_headers, json={"reason": "Снова"}
    )
    assert rejected.status_code == 200, rejected.text

    response = await post_kyc(client, headers)

    assert_error(response, 429, "too_many_requests")
    assert await kyc_count(db_session, user.id) == 2


# --- GET /kyc/my ---


async def test_my_lists_own_newest_first(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 5 plan: GET /kyc/my → свои заявки, новые первыми; чужих нет; без путей (R-04)."""
    _, mod_headers = await user_with_headers(db_session, client, UserRole.MODERATOR)
    user, headers, old = await rejected_user(client, db_session, mod_headers)
    now = datetime.now(UTC)
    await set_kyc_times(
        db_session,
        old["id"],
        submitted_at=now - timedelta(days=3),
        checked_at=now - timedelta(days=2),
    )
    new = await submit(client, headers)
    _, other_headers = await user_with_headers(db_session, client)
    await submit(client, other_headers)

    response = await client.get("/kyc/my", headers=headers)

    assert response.status_code == 200, response.text
    data = response.json()
    assert [item["id"] for item in data] == [new["id"], old["id"]]
    for item in data:
        assert_kyc_out(item)
        assert item["user_id"] == user.id
    assert data[1]["status"] == "rejected"
    assert data[1]["rejection_reason"] == "Нечитаемо"
    assert data[1]["checked_at"] is not None
    assert_no_doc_paths(response, await kyc_row(db_session, new["id"]))


async def test_my_ties_broken_by_id_desc(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 5 plan (граница): одинаковый submitted_at → порядок по id DESC."""
    _, mod_headers = await user_with_headers(db_session, client, UserRole.MODERATOR)
    _, headers, old = await rejected_user(client, db_session, mod_headers)
    ts = datetime.now(UTC) - timedelta(days=1)
    await set_kyc_times(db_session, old["id"], submitted_at=ts, checked_at=ts)
    new = await submit(client, headers)
    await set_kyc_times(db_session, new["id"], submitted_at=ts)

    response = await client.get("/kyc/my", headers=headers)

    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()] == [new["id"], old["id"]]


async def test_my_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 5 plan (граница): заявок нет → 200 []."""
    _, headers = await user_with_headers(db_session, client)

    response = await client.get("/kyc/my", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == []


# --- GET /kyc (очередь модератора) ---


@pytest.mark.parametrize("role", MODERATOR_ROLES)
async def test_queue_pending_oldest_first(
    client: AsyncClient, db_session: AsyncSession, role: UserRole
) -> None:
    """Step 5 plan: GET /kyc?status=pending (moderator/admin) → только pending,
    старые первыми; без путей к документам (R-04)."""
    _, mod_headers = await user_with_headers(db_session, client, role)
    now = datetime.now(UTC)
    ids = []
    for _ in range(3):
        _, headers = await user_with_headers(db_session, client)
        ids.append((await submit(client, headers))["id"])
    # Первая поданная — самая новая, вторая — самая старая.
    await set_kyc_times(db_session, ids[0], submitted_at=now - timedelta(hours=1))
    await set_kyc_times(db_session, ids[1], submitted_at=now - timedelta(hours=3))
    await set_kyc_times(db_session, ids[2], submitted_at=now - timedelta(hours=2))
    _, done_headers = await user_with_headers(db_session, client)
    done = await submit(client, done_headers)
    approved = await client.post(f"/kyc/{done['id']}/approve", headers=mod_headers)
    assert approved.status_code == 200, approved.text

    response = await client.get("/kyc", headers=mod_headers, params={"status": "pending"})

    assert response.status_code == 200, response.text
    data = response.json()
    assert [item["id"] for item in data] == [ids[1], ids[2], ids[0]]
    for item in data:
        assert_kyc_out(item)
        assert item["status"] == "pending"
    for kyc_id in ids:
        assert_no_doc_paths(response, await kyc_row(db_session, kyc_id))


async def test_queue_without_status_returns_all(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 5 plan: GET /kyc без status → все заявки (pending, approved, rejected)."""
    _, mod_headers = await user_with_headers(db_session, client, UserRole.MODERATOR)
    _, h1 = await user_with_headers(db_session, client)
    _, h2 = await user_with_headers(db_session, client)
    _, h3 = await user_with_headers(db_session, client)
    pending = await submit(client, h1)
    approved = await submit(client, h2)
    rejected = await submit(client, h3)
    assert (await client.post(f"/kyc/{approved['id']}/approve", headers=mod_headers)).is_success
    assert (
        await client.post(
            f"/kyc/{rejected['id']}/reject", headers=mod_headers, json={"reason": "Нет"}
        )
    ).is_success

    response = await client.get("/kyc", headers=mod_headers)

    assert response.status_code == 200, response.text
    data = response.json()
    assert {item["id"]: item["status"] for item in data} == {
        pending["id"]: "pending",
        approved["id"]: "approved",
        rejected["id"]: "rejected",
    }
    for item in data:
        assert_kyc_out(item)


async def test_queue_filter_by_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 5 plan: GET /kyc?status=rejected → только отклонённые."""
    _, mod_headers = await user_with_headers(db_session, client, UserRole.MODERATOR)
    _, _, rejected = await rejected_user(client, db_session, mod_headers)
    _, headers = await user_with_headers(db_session, client)
    await submit(client, headers)

    response = await client.get("/kyc", headers=mod_headers, params={"status": "rejected"})

    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()] == [rejected["id"]]


async def test_queue_forbidden_for_user(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-04: обычный user не видит очередь → 403 permission_denied."""
    _, headers = await user_with_headers(db_session, client)
    await submit(client, headers)

    response = await client.get("/kyc", headers=headers, params={"status": "pending"})

    assert_error(response, 403, "permission_denied")


# --- GET /kyc/{id}/passport|selfie ---


@pytest.mark.parametrize(
    ("content", "media_type"),
    [(JPEG, "image/jpeg"), (PNG, "image/png"), (WEBP, "image/webp")],
    ids=["jpeg", "png", "webp"],
)
async def test_owner_downloads_documents(
    client: AsyncClient, db_session: AsyncSession, content: bytes, media_type: str
) -> None:
    """R-04: владелец получает свои документы побайтно, с типом по содержимому
    и Cache-Control: no-store."""
    _, headers = await user_with_headers(db_session, client)
    passport, selfie = unique(content), unique(content)
    kyc = await submit(client, headers, passport=passport, selfie=selfie)

    for doc, expected in (("passport", passport), ("selfie", selfie)):
        response = await client.get(f"/kyc/{kyc['id']}/{doc}", headers=headers)
        assert response.status_code == 200, response.text
        assert response.content == expected
        assert response.headers["content-type"].split(";")[0].strip() == media_type
        assert "no-store" in response.headers.get("cache-control", "")


@pytest.mark.parametrize("role", MODERATOR_ROLES)
async def test_moderator_downloads_documents(
    client: AsyncClient, db_session: AsyncSession, role: UserRole
) -> None:
    """R-04: moderator/admin получает документы чужой заявки."""
    _, headers = await user_with_headers(db_session, client)
    passport, selfie = unique(JPEG), unique(PNG)
    kyc = await submit(client, headers, passport=passport, selfie=selfie)
    _, mod_headers = await user_with_headers(db_session, client, role)

    for doc, expected, media_type in (
        ("passport", passport, "image/jpeg"),
        ("selfie", selfie, "image/png"),
    ):
        response = await client.get(f"/kyc/{kyc['id']}/{doc}", headers=mod_headers)
        assert response.status_code == 200, response.text
        assert response.content == expected
        assert response.headers["content-type"].split(";")[0].strip() == media_type
        assert "no-store" in response.headers.get("cache-control", "")


@pytest.mark.parametrize("doc", ["passport", "selfie"])
async def test_other_user_cannot_download_documents(
    client: AsyncClient, db_session: AsyncSession, doc: str
) -> None:
    """R-04: чужой user → 403 permission_denied, документ не отдаётся."""
    _, headers = await user_with_headers(db_session, client)
    passport, selfie = unique(JPEG), unique(PNG)
    kyc = await submit(client, headers, passport=passport, selfie=selfie)
    _, other_headers = await user_with_headers(db_session, client)

    response = await client.get(f"/kyc/{kyc['id']}/{doc}", headers=other_headers)

    assert_error(response, 403, "permission_denied")
    assert passport not in response.content
    assert selfie not in response.content


@pytest.mark.parametrize("doc", ["passport", "selfie"])
async def test_document_of_missing_kyc_not_found(
    client: AsyncClient, db_session: AsyncSession, doc: str
) -> None:
    """R-04: несуществующая заявка → 404 not_found (для модератора)."""
    _, mod_headers = await user_with_headers(db_session, client, UserRole.MODERATOR)

    response = await client.get(f"/kyc/999999/{doc}", headers=mod_headers)

    assert_error(response, 404, "not_found")


# --- POST /kyc/{id}/approve ---


@pytest.mark.parametrize("role", MODERATOR_ROLES)
async def test_approve(client: AsyncClient, db_session: AsyncSession, role: UserRole) -> None:
    """R-28, R-39, R-03: approve → 200 approved, reviewed_by, users.kyc_status = approved,
    одна запись аудита kyc_approved и уведомление владельцу kyc_approved."""
    moderator, mod_headers = await user_with_headers(db_session, client, role)
    user, headers = await user_with_headers(db_session, client)
    kyc = await submit(client, headers, resident_type="kg_resident")

    response = await client.post(f"/kyc/{kyc['id']}/approve", headers=mod_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert_kyc_out(body)
    assert body["id"] == kyc["id"]
    assert body["user_id"] == user.id
    assert body["status"] == "approved"
    assert body["resident_type"] == "kg_resident"
    assert body["rejection_reason"] is None
    assert body["checked_at"] is not None
    assert parse_ts(body["checked_at"]) >= parse_ts(body["submitted_at"])

    row = await kyc_row(db_session, kyc["id"])
    assert row.status == KycStatus.APPROVED
    assert row.reviewed_by == moderator.id
    assert row.checked_at is not None
    assert_no_doc_paths(response, row)
    assert await user_kyc_status(db_session, user.id) == KycStatus.APPROVED

    audit = await kyc_audit(db_session, kyc["id"])
    assert len(audit) == 1
    assert audit[0].action == "kyc_approved"
    assert audit[0].actor_id == moderator.id
    assert audit[0].entity_id == kyc["id"]

    notifications = await user_notifications(db_session, user.id)
    assert len(notifications) == 1
    assert notifications[0].type == "kyc_approved"
    assert notifications[0].channel == "in_app"
    assert notifications[0].payload == {"kyc_id": kyc["id"]}
    assert await user_notifications(db_session, moderator.id) == []


async def test_approve_notification_visible_via_api(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-39: уведомление о решении видно владельцу через GET /notifications."""
    _, mod_headers = await user_with_headers(db_session, client, UserRole.MODERATOR)
    _, headers = await user_with_headers(db_session, client)
    kyc = await submit(client, headers)
    assert (await client.post(f"/kyc/{kyc['id']}/approve", headers=mod_headers)).is_success

    response = await client.get("/notifications", headers=headers)

    assert response.status_code == 200, response.text
    data = response.json()
    assert [(n["type"], n["payload"]) for n in data] == [("kyc_approved", {"kyc_id": kyc["id"]})]


# --- POST /kyc/{id}/reject ---


@pytest.mark.parametrize("role", MODERATOR_ROLES)
async def test_reject(client: AsyncClient, db_session: AsyncSession, role: UserRole) -> None:
    """R-28, R-39: reject → 200 rejected с rejection_reason, users.kyc_status = rejected,
    аудит kyc_rejected с details.reason, уведомление kyc_rejected с причиной."""
    moderator, mod_headers = await user_with_headers(db_session, client, role)
    user, headers = await user_with_headers(db_session, client)
    kyc = await submit(client, headers)
    reason = "Фото паспорта размыто"

    response = await client.post(
        f"/kyc/{kyc['id']}/reject", headers=mod_headers, json={"reason": reason}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert_kyc_out(body)
    assert body["status"] == "rejected"
    assert body["rejection_reason"] == reason
    assert body["checked_at"] is not None

    row = await kyc_row(db_session, kyc["id"])
    assert row.status == KycStatus.REJECTED
    assert row.rejection_reason == reason
    assert row.reviewed_by == moderator.id
    assert_no_doc_paths(response, row)
    assert await user_kyc_status(db_session, user.id) == KycStatus.REJECTED

    audit = await kyc_audit(db_session, kyc["id"])
    assert len(audit) == 1
    assert audit[0].action == "kyc_rejected"
    assert audit[0].actor_id == moderator.id
    assert audit[0].details == {"reason": reason}

    notifications = await user_notifications(db_session, user.id)
    assert len(notifications) == 1
    assert notifications[0].type == "kyc_rejected"
    assert notifications[0].channel == "in_app"
    assert notifications[0].payload == {"kyc_id": kyc["id"], "reason": reason}


@pytest.mark.parametrize(
    "payload",
    [{"reason": ""}, {"reason": "   "}, {}, None],
    ids=["empty", "whitespace", "no-reason", "no-body"],
)
async def test_reject_requires_reason(
    client: AsyncClient, db_session: AsyncSession, payload: dict[str, str] | None
) -> None:
    """Step 5 plan: пустой/пробельный reason или без тела → 422 validation_error;
    заявка остаётся pending, аудита и уведомлений нет."""
    _, mod_headers = await user_with_headers(db_session, client, UserRole.MODERATOR)
    user, headers = await user_with_headers(db_session, client)
    kyc = await submit(client, headers)

    response = await client.post(f"/kyc/{kyc['id']}/reject", headers=mod_headers, json=payload)

    assert_error(response, 422, "validation_error")
    assert (await kyc_row(db_session, kyc["id"])).status == KycStatus.PENDING
    assert await user_kyc_status(db_session, user.id) == KycStatus.PENDING
    assert await audit_count(db_session) == 0
    assert await notification_count(db_session) == 0


# --- approve/reject: ошибки ---


@pytest.mark.parametrize("first", ["approve", "reject"])
@pytest.mark.parametrize("second", ["approve", "reject"])
async def test_decision_on_decided_kyc_conflict(
    client: AsyncClient, db_session: AsyncSession, first: str, second: str
) -> None:
    """R-28, R-39: решение по уже решённой заявке → 409 conflict, статус не меняется,
    новой записи аудита и уведомления нет."""
    _, mod_headers = await user_with_headers(db_session, client, UserRole.MODERATOR)
    user, headers = await user_with_headers(db_session, client)
    kyc = await submit(client, headers)
    body = {"reason": "Причина"}
    done = await client.post(f"/kyc/{kyc['id']}/{first}", headers=mod_headers, json=body)
    assert done.status_code == 200, done.text
    expected = KycStatus.APPROVED if first == "approve" else KycStatus.REJECTED
    audits, notifications = await audit_count(db_session), await notification_count(db_session)

    response = await client.post(f"/kyc/{kyc['id']}/{second}", headers=mod_headers, json=body)

    assert_error(response, 409, "conflict")
    assert (await kyc_row(db_session, kyc["id"])).status == expected
    assert await user_kyc_status(db_session, user.id) == expected
    assert await audit_count(db_session) == audits
    assert await notification_count(db_session) == notifications


@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_decision_on_missing_kyc_not_found(
    client: AsyncClient, db_session: AsyncSession, action: str
) -> None:
    """Step 5 plan: несуществующая заявка → 404 not_found."""
    _, mod_headers = await user_with_headers(db_session, client, UserRole.MODERATOR)

    response = await client.post(
        f"/kyc/999999/{action}", headers=mod_headers, json={"reason": "Причина"}
    )

    assert_error(response, 404, "not_found")


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("as_owner", [True, False], ids=["owner", "other-user"])
async def test_user_cannot_decide(
    client: AsyncClient, db_session: AsyncSession, action: str, as_owner: bool
) -> None:
    """R-28: обычный user (в т.ч. владелец) не решает KYC → 403 permission_denied;
    ничего не меняется."""
    user, headers = await user_with_headers(db_session, client)
    kyc = await submit(client, headers)
    if as_owner:
        actor_headers = headers
    else:
        _, actor_headers = await user_with_headers(db_session, client)

    response = await client.post(
        f"/kyc/{kyc['id']}/{action}", headers=actor_headers, json={"reason": "Причина"}
    )

    assert_error(response, 403, "permission_denied")
    row = await kyc_row(db_session, kyc["id"])
    assert row.status == KycStatus.PENDING
    assert row.reviewed_by is None
    assert row.checked_at is None
    assert await user_kyc_status(db_session, user.id) == KycStatus.PENDING
    assert await audit_count(db_session) == 0
    assert await notification_count(db_session) == 0


# --- R-04: /users/me ---


async def test_users_me_has_no_document_paths(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-04: GET /users/me не содержит путей и имён файлов KYC-документов."""
    _, headers = await user_with_headers(db_session, client)
    kyc = await submit(client, headers)

    response = await client.get("/users/me", headers=headers)

    assert response.status_code == 200, response.text
    assert_no_doc_paths(response, await kyc_row(db_session, kyc["id"]))


async def test_invalid_selfie_leaves_no_passport_file(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-04: при отказе из-за селфи сохранённый паспорт удаляется — документов без заявки нет."""
    _, headers = await user_with_headers(db_session, client)
    passport = unique(JPEG)

    response = await post_kyc(client, headers, passport=passport, selfie=b"not an image")

    assert_error(response, 400, "invalid_file")
    assert files_with_content(storage_dir() / "private", passport) == []


async def test_parallel_submissions_create_one_kyc(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Решение 2026-10-05: две одновременные подачи — одна заявка, вторая 409 (блокировка user)."""
    user, headers = await user_with_headers(db_session, client)

    responses = await asyncio.gather(post_kyc(client, headers), post_kyc(client, headers))

    assert sorted(r.status_code for r in responses) == [201, 409]
    assert await kyc_count(db_session, user.id) == 1


async def test_parallel_decisions_only_one_applies(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-28, R-39: одновременные approve и reject одной заявки — применяется одно решение."""
    _, headers = await user_with_headers(db_session, client)
    _, mod_headers = await user_with_headers(db_session, client, UserRole.MODERATOR)
    kyc = await submit(client, headers)

    responses = await asyncio.gather(
        client.post(f"/kyc/{kyc['id']}/approve", headers=mod_headers),
        client.post(f"/kyc/{kyc['id']}/reject", headers=mod_headers, json={"reason": "Нечитаемо"}),
    )

    assert sorted(r.status_code for r in responses) == [200, 409]
    assert len(await kyc_audit(db_session, kyc["id"])) == 1
    assert await notification_count(db_session) == 1


async def test_failed_commit_leaves_no_document_files(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-04: если заявка не сохранилась в БД, файлы документов удаляются с диска."""
    _, headers = await user_with_headers(db_session, client)
    passport, selfie = unique(JPEG), unique(PNG)

    async def failing_commit(self: AsyncSession) -> None:
        raise RuntimeError("db is down")

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)
    with pytest.raises(RuntimeError):
        await post_kyc(client, headers, passport=passport, selfie=selfie)
    monkeypatch.undo()

    private = storage_dir() / "private"
    assert files_with_content(private, passport) == []
    assert files_with_content(private, selfie) == []
