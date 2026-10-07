from collections.abc import Sequence
from datetime import date

from sqlalchemy import ColumnElement, and_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.clock import today_bishkek
from app.core.errors import ConflictError, NotFoundError
from app.modules.activities.models import Activity, activity_tags
from app.modules.admin.models import Region
from app.modules.hosts.models import HostProfile
from app.modules.hosts.schemas import HostProfileCreate
from app.modules.users.models import KycStatus, User, UserStatus


async def create_profile(
    session: AsyncSession, user_id: int, data: HostProfileCreate
) -> HostProfile:
    """Профиль целиком (R-29), сразу опубликован (решение #9); в каталоге — при KYC (R-03)."""
    if await session.get(HostProfile, user_id) is not None:
        raise ConflictError("Host profile already exists")
    if await session.get(Region, data.region_id) is None:
        raise NotFoundError("Region not found")

    video_duration_sec = None
    if data.video_url is not None:
        # R-07: длительность — из самого файла, клиенту не доверяем.
        video_duration_sec = await storage.video_duration_sec(
            data.video_url.removeprefix("/files/")
        )
        if video_duration_sec is None or not 1 <= video_duration_sec <= 120:
            raise storage.InvalidFileError("Video must be an MP4 up to 120 seconds")

    profile = HostProfile(
        user_id=user_id,
        **data.model_dump(),
        video_duration_sec=video_duration_sec,
        is_published=True,
    )
    session.add(profile)
    try:
        await session.commit()
    except IntegrityError as exc:  # параллельное создание того же профиля
        await session.rollback()
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise ConflictError("Host profile already exists") from exc
        raise
    await session.refresh(profile)
    return profile


async def get_own_profile(session: AsyncSession, user_id: int) -> HostProfile:
    profile = await session.get(HostProfile, user_id)
    if profile is None:
        raise NotFoundError("Host profile not found")
    return profile


def visible_host() -> ColumnElement[bool]:
    """Публично виден опубликованный хозяин с одобренным KYC (R-03), не заблокированный."""
    return and_(
        HostProfile.is_published.is_(True),
        User.kyc_status == KycStatus.APPROVED,
        User.status == UserStatus.ACTIVE,
    )


async def get_public_profile(session: AsyncSession, host_id: int) -> HostProfile:
    profile = await session.scalar(
        select(HostProfile)
        .join(User, User.id == HostProfile.user_id)
        .where(HostProfile.user_id == host_id, visible_host())
    )
    if profile is None:
        raise NotFoundError("Host not found")
    return profile


async def list_catalog(
    session: AsyncSession, region_id: int | None, tag_ids: list[int]
) -> Sequence[HostProfile]:
    """Каталог; фильтр по тегам — есть активное занятие с одним из тегов (R-09)."""
    query = (
        select(HostProfile)
        .join(User, User.id == HostProfile.user_id)
        .where(visible_host())
        .order_by(HostProfile.user_id)
    )
    if region_id is not None:
        query = query.where(HostProfile.region_id == region_id)
    if tag_ids:
        query = query.where(
            select(Activity.id)
            .join(activity_tags, activity_tags.c.activity_id == Activity.id)
            .where(
                Activity.host_id == HostProfile.user_id,
                Activity.is_active.is_(True),
                activity_tags.c.tag_id.in_(tag_ids),
            )
            .exists()
        )
    return (await session.scalars(query)).all()


# Активные брони stay хозяина: просроченный hold мест не держит (R-12, R-20).
_ACTIVE_STAY = """
    b.type = 'stay' AND b.host_id = :host
    AND b.status IN ('hold', 'confirmed', 'in_progress')
    AND (b.status <> 'hold' OR b.hold_expires_at > now())
"""

# Дни из списка, на которые есть активные брони (ночь d в [start_date, end_date)).
_BOOKED_DATES_SQL = text(
    f"""
    SELECT DISTINCT d FROM unnest(CAST(:dates AS date[])) AS d
    JOIN bookings b ON {_ACTIVE_STAY} AND b.start_date <= d AND d < b.end_date
    ORDER BY d
    """
)

# Максимум забронированных мест на одну ночь, начиная с сегодняшней.
_MAX_BOOKED_SQL = text(
    f"""
    SELECT COALESCE(MAX(booked), 0) FROM (
      SELECT SUM(b.guests_count) AS booked
      FROM bookings b
      CROSS JOIN generate_series(GREATEST(b.start_date, CAST(:today AS date)),
                                 b.end_date - 1, interval '1 day') AS n
      WHERE {_ACTIVE_STAY} AND b.end_date > :today
      GROUP BY n
    ) AS nights
    """
)


async def _lock_own_profile(session: AsyncSession, user_id: int) -> HostProfile:
    """Изменение доступности — под FOR UPDATE строки хозяина, как и бронь (R-16)."""
    profile = await session.scalar(
        select(HostProfile).where(HostProfile.user_id == user_id).with_for_update()
    )
    if profile is None:
        raise NotFoundError("Host profile not found")
    return profile


async def set_blocked_dates(session: AsyncSession, user_id: int, dates: list[date]) -> HostProfile:
    """Закрыть можно только день без активных броней; прошедшие даты удаляются (R-14)."""
    profile = await _lock_own_profile(session, user_id)
    today = today_bishkek()
    new_dates = sorted({d for d in dates if d >= today})
    booked = (await session.scalars(_BOOKED_DATES_SQL, {"host": user_id, "dates": new_dates})).all()
    if booked:
        days = ", ".join(d.isoformat() for d in booked)
        raise ConflictError(f"Dates have active bookings: {days}")
    profile.blocked_dates = new_dates
    await session.commit()
    await session.refresh(profile)
    return profile


async def set_capacity(session: AsyncSession, user_id: int, capacity: int) -> HostProfile:
    """Нельзя опустить вместимость ниже забронированного на любую будущую ночь (R-15)."""
    profile = await _lock_own_profile(session, user_id)
    booked = await session.scalar(_MAX_BOOKED_SQL, {"host": user_id, "today": today_bishkek()})
    if capacity < (booked or 0):
        raise ConflictError(f"{booked} seats are already booked for a future night")
    profile.capacity = capacity
    await session.commit()
    await session.refresh(profile)
    return profile
