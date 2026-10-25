from fastapi import APIRouter, status
from sqlalchemy import select

from app.deps import Session
from app.errors import bad_request, conflict, not_found
from app.models import Favorite, HostProfile, Place, Vehicle
from app.pagination import Page, Pagination, paginate
from app.schemas.social import FavoriteCreate, FavoriteOut
from app.security import CurrentUser

router = APIRouter(prefix="/favorites", tags=["favorites"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_favorite(data: FavoriteCreate, me: CurrentUser, session: Session) -> FavoriteOut:
    """Добавить в избранное хозяина, место или авто (ровно одно)."""
    model, target_id = (
        (HostProfile, data.host_id)
        if data.host_id is not None
        else (Place, data.place_id)
        if data.place_id is not None
        else (Vehicle, data.vehicle_id)
    )
    if await session.get(model, target_id) is None:
        raise bad_request("Объект не найден")
    exists = await session.scalar(
        select(Favorite.id).where(
            Favorite.user_id == me.id,
            Favorite.host_id.is_not_distinct_from(data.host_id),
            Favorite.place_id.is_not_distinct_from(data.place_id),
            Favorite.vehicle_id.is_not_distinct_from(data.vehicle_id),
        )
    )
    if exists:
        raise conflict("Уже в избранном")
    favorite = Favorite(user_id=me.id, **data.model_dump())
    session.add(favorite)
    await session.commit()
    await session.refresh(favorite)
    return FavoriteOut.model_validate(favorite)


@router.get("")
async def list_favorites(me: CurrentUser, session: Session, page: Pagination) -> Page[FavoriteOut]:
    query = select(Favorite).where(Favorite.user_id == me.id).order_by(Favorite.id.desc())
    return Page[FavoriteOut].model_validate(await paginate(session, query, page))


@router.delete("/{favorite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_favorite(favorite_id: int, me: CurrentUser, session: Session) -> None:
    favorite = await session.get(Favorite, favorite_id)
    if favorite is None or favorite.user_id != me.id:
        raise not_found("Нет в избранном")
    await session.delete(favorite)
    await session.commit()
