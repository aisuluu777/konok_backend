from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.guests.models import GuestProfile
from app.modules.guests.schemas import GuestProfileIn


async def get_profile(session: AsyncSession, user_id: int) -> GuestProfile:
    profile = await session.get(GuestProfile, user_id)
    if profile is None:
        raise NotFoundError("Guest profile not found")
    return profile


async def put_profile(session: AsyncSession, user_id: int, data: GuestProfileIn) -> GuestProfile:
    """Полная замена своего профиля; создаёт, если его нет (атомарно, без гонки)."""
    values = data.model_dump()
    stmt = (
        insert(GuestProfile)
        .values(user_id=user_id, **values)
        .on_conflict_do_update(
            index_elements=[GuestProfile.user_id], set_={**values, "updated_at": func.now()}
        )
        .returning(GuestProfile)
    )
    profile = await session.scalar(stmt, execution_options={"populate_existing": True})
    await session.commit()
    assert profile is not None
    return profile
