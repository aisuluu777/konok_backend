"""Уведомления: запись в таблицу notifications (GET API) и web push на все браузеры пользователя."""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from pywebpush import WebPushException, webpush
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Notification, PushSubscription

logger = logging.getLogger(__name__)


def _send(subscription: PushSubscription, payload: str) -> None:
    settings = get_settings()
    webpush(
        subscription_info={
            "endpoint": subscription.endpoint,
            "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
        },
        data=payload,
        vapid_private_key=settings.vapid_private_key,
        vapid_claims={"sub": settings.vapid_subject},
        timeout=10,
    )


def notify(session: AsyncSession, user_id: int, type: str, data: dict[str, Any]) -> None:
    """Уведомление пользователю. Сохраняется в одной транзакции с событием (коммит — за
    вызывающим); web push отправит воркер (send_pushes)."""
    session.add(Notification(user_id=user_id, type=type, data=data))


async def send_pushes(session: AsyncSession, now: datetime) -> int:
    """Отправляет web push по всем уведомлениям, которые ещё не отправлены."""
    notifications = (
        await session.scalars(
            select(Notification)
            .where(Notification.pushed_at.is_(None))
            .order_by(Notification.id)
            .limit(500)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for notification in notifications:
        if get_settings().vapid_private_key:
            payload = {"id": notification.id, "type": notification.type, "data": notification.data}
            await _push(session, notification.user_id, payload)
        notification.pushed_at = now
    await session.commit()
    return len(notifications)


async def _push(session: AsyncSession, user_id: int, payload: dict[str, Any]) -> None:
    subscriptions = await session.scalars(
        select(PushSubscription).where(PushSubscription.user_id == user_id)
    )
    for subscription in subscriptions:
        try:
            await asyncio.to_thread(_send, subscription, json.dumps(payload))
        except WebPushException as exc:
            # 404 / 410 — браузер отписался: подписка больше не нужна.
            if exc.response is not None and exc.response.status_code in (404, 410):
                await session.delete(subscription)
            else:
                logger.warning("Web push не отправлен (user %s): %s", user_id, exc)
