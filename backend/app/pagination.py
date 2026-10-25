"""Единый формат пагинации: ?limit=&offset= → {"items", "total", "limit", "offset"}."""

from typing import Annotated, Any

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession


class PageParams(BaseModel):
    limit: int
    offset: int


def page_params(
    limit: Annotated[int, Query(ge=1, le=100, description="Сколько записей вернуть")] = 20,
    offset: Annotated[int, Query(ge=0, description="Сколько записей пропустить")] = 0,
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


Pagination = Annotated[PageParams, Depends(page_params)]


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


async def paginate(session: AsyncSession, query: Select[Any], params: PageParams) -> dict[str, Any]:
    """Выполняет запрос со смещением; items — ORM-объекты (схема ответа их преобразует)."""
    total = await session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = await session.scalars(query.limit(params.limit).offset(params.offset))
    return {
        "items": list(rows.unique()),
        "total": total or 0,
        "limit": params.limit,
        "offset": params.offset,
    }
