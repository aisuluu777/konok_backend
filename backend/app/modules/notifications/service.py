from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.notifications.models import IN_APP, Notification


def create_notification(
    session: AsyncSession, *, user_id: int, type: str, payload: dict[str, Any] | None = None
) -> Notification:
    """Добавляет in-app уведомление в транзакцию события; коммитит вызывающий сервис (R-39)."""
    notification = Notification(user_id=user_id, type=type, channel=IN_APP, payload=payload or {})
    session.add(notification)
    return notification


async def list_notifications(session: AsyncSession, user_id: int) -> Sequence[Notification]:
    query = (
        select(Notification)
        .where(Notification.user_id == user_id, Notification.channel == IN_APP)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
    )
    return (await session.scalars(query)).all()


async def mark_read(session: AsyncSession, user_id: int, notification_id: int) -> Notification:
    # Чужое уведомление — тот же 404, что и несуществующее: не раскрываем чужие id.
    notification = await session.scalar(
        select(Notification)
        .where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
            Notification.channel == IN_APP,
        )
        .with_for_update()  # параллельная отметка не перезапишет первое read_at
    )
    if notification is None:
        raise NotFoundError("Notification not found")
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        await session.commit()
    return notification


async def mark_all_read(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.channel == IN_APP,
            Notification.read_at.is_(None),
        )
        .values(read_at=datetime.now(UTC))
    )
    await session.commit()
    return result.rowcount  # type: ignore[attr-defined,no-any-return]
