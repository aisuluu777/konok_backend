from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PermissionDeniedError
from app.modules.activities.models import Activity
from app.modules.activities.schemas import ActivityCreate, ActivityUpdate
from app.modules.admin.models import Tag
from app.modules.hosts import service as hosts_service
from app.modules.hosts.models import HostProfile


async def _tags(session: AsyncSession, tag_ids: list[int]) -> list[Tag]:
    """Теги только из админского списка (R-09); повторы в запросе схлопываются."""
    wanted = set(tag_ids)
    tags = list((await session.scalars(select(Tag).where(Tag.id.in_(wanted)))).all())
    if len(tags) != len(wanted):
        raise NotFoundError("Tag not found")
    return tags

# TODO: зачем это провекрка всё равно пользователь не может получить чужое занятие, так как в личных кабинетах только свои занятия, а в публичных только активные занятия видимого хозяина. Поэтому проверка на чужое занятие не нужна.
async def _own_activity(session: AsyncSession, user_id: int, activity_id: int) -> Activity:
    """Чужое занятие — 404, как и несуществующее."""
    activity = await session.get(Activity, activity_id)
    if activity is None or activity.host_id != user_id:
        raise NotFoundError("Activity not found")
    return activity


async def create_activity(session: AsyncSession, user_id: int, data: ActivityCreate) -> Activity:
    """Занятие создаёт только хозяин (R-02, R-08)."""
    if await session.get(HostProfile, user_id) is None:
        raise PermissionDeniedError("Only hosts can create activities")
    activity = Activity(
        host_id=user_id,
        name=data.name,
        description=data.description,
        price_per_person=data.price_per_person,
        tags=await _tags(session, data.tag_ids),
    )
    session.add(activity)
    await session.commit()
    await session.refresh(activity)
    return activity


async def update_activity(
    session: AsyncSession, user_id: int, activity_id: int, data: ActivityUpdate
) -> Activity:
    """Цена в купленных занятиях — снимок (R-10), поэтому её можно менять."""
    activity = await _own_activity(session, user_id, activity_id)
    changes = data.model_dump(exclude_unset=True)
    for field in ("name", "price_per_person", "description"):
        if field in changes and (changes[field] is not None or field == "description"):
            setattr(activity, field, changes[field])
    if data.tag_ids is not None:
        activity.tags = await _tags(session, data.tag_ids)
        activity.updated_at = func.now()  # теги — в activity_tags, onupdate сам не сработает
    await session.commit()
    await session.refresh(activity)
    return activity


async def deactivate_activity(session: AsyncSession, user_id: int, activity_id: int) -> Activity:
    """Удаления нет — только скрытие (R-10). Повторная деактивация ничего не меняет."""
    activity = await _own_activity(session, user_id, activity_id)
    if activity.is_active:
        activity.is_active = False
        await session.commit()
        await session.refresh(activity)
    return activity


async def list_own_activities(session: AsyncSession, user_id: int) -> Sequence[Activity]:
    """Свои занятия, включая скрытые."""
    query = select(Activity).where(Activity.host_id == user_id).order_by(Activity.id)
    return (await session.scalars(query)).all()


async def list_host_activities(session: AsyncSession, host_id: int) -> Sequence[Activity]:
    """Публично — только активные занятия видимого хозяина (R-03, R-10)."""
    await hosts_service.get_public_profile(session, host_id)
    query = (
        select(Activity)
        .where(Activity.host_id == host_id, Activity.is_active.is_(True))
        .order_by(Activity.id)
    )
    return (await session.scalars(query)).all()
