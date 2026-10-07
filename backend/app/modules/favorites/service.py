from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.admin.service import get_place
from app.modules.favorites.models import Favorite
from app.modules.favorites.schemas import FavoriteCreate
from app.modules.hosts.service import get_public_profile
from app.modules.vehicles.service import get_public_vehicle


async def add_favorite(session: AsyncSession, user_id: int, data: FavoriteCreate) -> Favorite:
    """Добавляет объект в избранное (R-38): виден публично, повтор → 409."""
    if data.host_id is not None:
        await get_public_profile(session, data.host_id)
    elif data.vehicle_id is not None:
        await get_public_vehicle(session, data.vehicle_id)
    elif data.place_id is not None:
        await get_place(session, data.place_id)
    favorite = Favorite(
        user_id=user_id,
        host_id=data.host_id,
        place_id=data.place_id,
        vehicle_id=data.vehicle_id,
    )
    session.add(favorite)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if getattr(exc.orig, "sqlstate", None) == "23505":  # уникальность (пользователь, объект)
            raise ConflictError("Already in favorites") from exc
        raise
    await session.refresh(favorite)
    return favorite


async def list_favorites(session: AsyncSession, user_id: int) -> Sequence[Favorite]:
    rows = await session.scalars(
        select(Favorite)
        .where(Favorite.user_id == user_id)
        .order_by(Favorite.created_at.desc(), Favorite.id.desc())
    )
    return rows.all()


async def delete_favorite(session: AsyncSession, user_id: int, favorite_id: int) -> None:
    favorite = await session.get(Favorite, favorite_id)
    if favorite is None or favorite.user_id != user_id:
        raise NotFoundError("Favorite not found")
    await session.delete(favorite)
    await session.commit()
