"""Фоновые задачи (arq + Redis). Запуск: arq app.worker.WorkerSettings"""

from datetime import time, timedelta
from typing import Any

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import delete, or_, select, update

from app import rules
from app.config import get_settings
from app.db import SessionFactory
from app.models import Booking, BookingActivity, BookingStatus, Payment
from app.notify import notify, send_pushes

REMINDER_EVERY = timedelta(hours=8)


async def remind_hosts(ctx: dict[str, Any]) -> int:
    """Напоминание хозяину: сразу после оплаты брони, затем каждые 8 часов,
    пока он не нажмёт «Вижу, жду гостя». Ночной паузы нет."""
    now = rules.now_bishkek()
    async with SessionFactory() as session:
        bookings = (
            await session.scalars(
                select(Booking)
                .where(
                    Booking.status == BookingStatus.CONFIRMED,
                    Booking.host_id.is_not(None),
                    Booking.host_acknowledged_at.is_(None),
                    or_(
                        Booking.last_reminder_at.is_(None),
                        Booking.last_reminder_at <= now - REMINDER_EVERY,
                    ),
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
        for booking in bookings:
            assert booking.host_id is not None
            notify(session, booking.host_id, "host_reminder", {"booking_id": booking.id})
            booking.last_reminder_at = now
        await session.commit()
    return len(bookings)


async def close_bookings(ctx: dict[str, Any]) -> int:
    """В время выезда (12:00) дня end_date: заселённые брони → completed,
    оплаченные, но без заселения по PIN → failed."""
    now = rules.now_bishkek()
    async with SessionFactory() as session:
        checkout = time.fromisoformat(await rules.get_setting(session, "checkout_time"))
        ended = (
            Booking.end_date <= now.date()
            if now.time() >= checkout
            else (Booking.end_date < now.date())
        )
        closed = 0
        for old, new in (
            (BookingStatus.IN_PROGRESS, BookingStatus.COMPLETED),
            (BookingStatus.CONFIRMED, BookingStatus.FAILED),
        ):
            result = await session.execute(
                update(Booking).where(Booking.status == old, ended).values(status=new)
            )
            closed += result.rowcount
        await session.commit()
    return closed


async def expire_payments(ctx: dict[str, Any]) -> int:
    """Бронь, которую не оплатили за payment_minutes (30 мин), удаляется — места свободны."""
    now = rules.now_bishkek()
    async with SessionFactory() as session:
        minutes = int(await rules.get_setting(session, "payment_minutes"))
        expired = (
            select(Booking.id)
            .where(
                Booking.status == BookingStatus.PENDING_PAYMENT,
                Booking.created_at <= now - timedelta(minutes=minutes),
            )
            .scalar_subquery()
        )
        await session.execute(
            delete(BookingActivity).where(BookingActivity.booking_id.in_(expired))
        )
        await session.execute(delete(Payment).where(Payment.booking_id.in_(expired)))
        result = await session.execute(delete(Booking).where(Booking.id.in_(expired)))
        await session.commit()
    return result.rowcount


async def push_notifications(ctx: dict[str, Any]) -> int:
    async with SessionFactory() as session:
        return await send_pushes(session, rules.now_bishkek())


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    functions: list[Any] = []
    cron_jobs = [
        cron(push_notifications, second=set(range(0, 60, 10))),  # каждые 10 секунд
        cron(remind_hosts, second=0),  # каждую минуту — «сразу после брони»
        cron(expire_payments, second=15),  # каждую минуту
        cron(close_bookings, minute=set(range(0, 60, 5)), second=30),  # каждые 5 минут
    ]
