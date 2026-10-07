from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BigId, get_session
from app.core.security import CurrentUser, get_current_user
from app.modules.favorites import service
from app.modules.favorites.schemas import FavoriteCreate, FavoriteOut

Session = Annotated[AsyncSession, Depends(get_session)]
Me = Annotated[CurrentUser, Depends(get_current_user)]

router = APIRouter(prefix="/favorites", tags=["favorites"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_favorite(data: FavoriteCreate, me: Me, session: Session) -> FavoriteOut:
    return FavoriteOut.model_validate(await service.add_favorite(session, me.id, data))


@router.get("")
async def list_favorites(me: Me, session: Session) -> list[FavoriteOut]:
    return [FavoriteOut.model_validate(f) for f in await service.list_favorites(session, me.id)]


@router.delete("/{favorite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_favorite(favorite_id: BigId, me: Me, session: Session) -> None:
    await service.delete_favorite(session, me.id, favorite_id)
