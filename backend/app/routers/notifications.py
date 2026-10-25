from fastapi import APIRouter, status
from sqlalchemy import delete, select

from app import rules
from app.config import get_settings
from app.deps import Session
from app.errors import not_found
from app.models import Notification, PushSubscription
from app.pagination import Page, Pagination, paginate
from app.schemas.notifications import (
    NotificationOut,
    PushSubscriptionIn,
    PushUnsubscribeIn,
    VapidKeyOut,
)
from app.security import CurrentUser

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    me: CurrentUser, session: Session, page: Pagination, unread_only: bool = False
) -> Page[NotificationOut]:
    """Мои уведомления, новые первыми."""
    query = (
        select(Notification).where(Notification.user_id == me.id).order_by(Notification.id.desc())
    )
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    return Page[NotificationOut].model_validate(await paginate(session, query, page))


@router.post("/{notification_id}/read")
async def mark_read(notification_id: int, me: CurrentUser, session: Session) -> NotificationOut:
    notification = await session.get(Notification, notification_id)
    if notification is None or notification.user_id != me.id:
        raise not_found("Уведомление не найдено")
    if notification.read_at is None:
        notification.read_at = rules.now_bishkek()
        await session.commit()
    return NotificationOut.model_validate(notification)


@router.get("/push/public-key")
async def vapid_public_key() -> VapidKeyOut:
    """Публичный VAPID-ключ для подписки браузера на web push."""
    return VapidKeyOut(public_key=get_settings().vapid_public_key)


@router.post("/push/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
async def subscribe(data: PushSubscriptionIn, me: CurrentUser, session: Session) -> None:
    """Сохранить подписку браузера. Повторная подписка того же браузера её обновляет."""
    subscription = await session.scalar(
        select(PushSubscription).where(PushSubscription.endpoint == data.endpoint)
    )
    if subscription is None:
        subscription = PushSubscription(endpoint=data.endpoint)
        session.add(subscription)
    subscription.user_id = me.id
    subscription.p256dh = data.keys.p256dh
    subscription.auth = data.keys.auth
    await session.commit()


@router.post("/push/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe(data: PushUnsubscribeIn, me: CurrentUser, session: Session) -> None:
    await session.execute(
        delete(PushSubscription).where(
            PushSubscription.endpoint == data.endpoint, PushSubscription.user_id == me.id
        )
    )
    await session.commit()
