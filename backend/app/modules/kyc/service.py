from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.audit import write_audit
from app.core.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    TooManyRequestsError,
)
from app.core.security import CurrentUser
from app.modules.admin.service import get_int_setting
from app.modules.kyc.models import KycVerification, ResidentType
from app.modules.notifications.service import create_notification
from app.modules.users.models import KycStatus, User, UserRole
from app.modules.users.service import new_pin

REVIEWERS = (UserRole.MODERATOR, UserRole.ADMIN)
Document = Literal["passport", "selfie"]


async def submit(
    session: AsyncSession,
    user_id: int,
    passport: BinaryIO,
    selfie: BinaryIO,
    resident_type: ResidentType | None,
) -> KycVerification:
    """Подача: из not_started, или после отказа через kyc_resubmit_hours (решение 2026-10-05)."""
    # Блокировка строки пользователя: две параллельные подачи не создадут две заявки.
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise NotFoundError("User not found")
    if user.kyc_status in (KycStatus.PENDING, KycStatus.APPROVED):
        raise ConflictError("KYC is already pending or approved")
    if user.kyc_status == KycStatus.REJECTED:
        last_checked = await session.scalar(
            select(KycVerification.checked_at)
            .where(KycVerification.user_id == user_id)
            .order_by(KycVerification.submitted_at.desc(), KycVerification.id.desc())
            .limit(1)
        )
        wait = timedelta(hours=await get_int_setting(session, "kyc_resubmit_hours"))
        if last_checked is not None and datetime.now(UTC) < last_checked + wait:
            raise TooManyRequestsError("KYC can be resubmitted later")

    passport_name = await storage.save(passport, "private", storage.IMAGES)
    try:
        selfie_name = await storage.save(selfie, "private", storage.IMAGES)
    except BaseException:
        storage.delete(passport_name, "private")  # не оставлять паспорт без заявки
        raise
    verification = KycVerification(
        user_id=user_id,
        resident_type=resident_type,
        passport_file_url=passport_name,
        selfie_file_url=selfie_name,
    )
    session.add(verification)
    user.kyc_status = KycStatus.PENDING
    try:
        await session.commit()
    except BaseException:
        # Заявки нет — документы без владельца на диске не оставляем.
        storage.delete(passport_name, "private")
        storage.delete(selfie_name, "private")
        raise
    await session.refresh(verification)
    return verification


async def list_own(session: AsyncSession, user_id: int) -> Sequence[KycVerification]:
    query = (
        select(KycVerification)
        .where(KycVerification.user_id == user_id)
        .order_by(KycVerification.submitted_at.desc(), KycVerification.id.desc())
    )
    return (await session.scalars(query)).all()


async def list_queue(session: AsyncSession, status: KycStatus | None) -> Sequence[KycVerification]:
    query = select(KycVerification).order_by(KycVerification.submitted_at, KycVerification.id)
    if status is not None:
        query = query.where(KycVerification.status == status)
    return (await session.scalars(query)).all()


async def document_path(
    session: AsyncSession, viewer: CurrentUser, kyc_id: int, document: Document
) -> tuple[Path, str]:
    """Файл документа — только владельцу и moderator/admin (R-04)."""
    verification = await session.get(KycVerification, kyc_id)
    if verification is None:
        raise NotFoundError("KYC verification not found")
    if verification.user_id != viewer.id and viewer.role not in REVIEWERS:
        raise PermissionDeniedError("No access to KYC documents")
    name = (
        verification.passport_file_url if document == "passport" else verification.selfie_file_url
    )
    found = storage.resolve(name, "private") if name else None
    if found is None:
        raise NotFoundError("Document not found")
    return found


async def decide(
    session: AsyncSession,
    *,
    reviewer_id: int,
    kyc_id: int,
    approve: bool,
    reason: str | None = None,
) -> KycVerification:
    """Решение модератора: статус проверки → копия в users, журнал (R-28), уведомление (R-39)."""
    verification = await session.scalar(
        select(KycVerification).where(KycVerification.id == kyc_id).with_for_update()
    )
    if verification is None:
        raise NotFoundError("KYC verification not found")
    if verification.status != KycStatus.PENDING:
        raise ConflictError("KYC verification is already decided")
    user = await session.scalar(
        select(User).where(User.id == verification.user_id).with_for_update()
    )
    assert user is not None  # FK + ON DELETE CASCADE

    status = KycStatus.APPROVED if approve else KycStatus.REJECTED
    verification.status = status
    verification.reviewed_by = reviewer_id
    verification.checked_at = datetime.now(UTC)
    verification.rejection_reason = reason
    user.kyc_status = status
    if approve and user.checkin_pin is None:
        user.checkin_pin = new_pin()  # R-23: PIN выдаётся при одобрении KYC

    event = "kyc_approved" if approve else "kyc_rejected"
    write_audit(
        session,
        actor_id=reviewer_id,
        action=event,
        entity_type="kyc_verification",
        entity_id=verification.id,
        details={} if approve else {"reason": reason},
    )
    payload: dict[str, object] = {"kyc_id": verification.id}
    if not approve:
        payload["reason"] = reason
    create_notification(session, user_id=user.id, type=event, payload=payload)
    await session.commit()
    return verification
