"""Справочники: регионы, локации, теги, места. Чтение без авторизации."""

from fastapi import APIRouter
from sqlalchemy import select

from app.deps import Session
from app.errors import not_found
from app.models import Locality, Place, Region, Tag
from app.pagination import Page, Pagination, paginate
from app.schemas.catalog import LocalityOut, PlaceOut, RegionOut, TagOut

router = APIRouter(tags=["catalog"])


@router.get("/regions")
async def list_regions(session: Session, page: Pagination) -> Page[RegionOut]:
    query = select(Region).where(Region.is_active).order_by(Region.id)
    return Page[RegionOut].model_validate(await paginate(session, query, page))


@router.get("/localities")
async def list_localities(
    session: Session, page: Pagination, region_id: int | None = None
) -> Page[LocalityOut]:
    """Активные локации активных регионов."""
    query = (
        select(Locality)
        .join(Region)
        .where(Locality.is_active, Region.is_active)
        .order_by(Locality.id)
    )
    if region_id is not None:
        query = query.where(Locality.region_id == region_id)
    return Page[LocalityOut].model_validate(await paginate(session, query, page))


@router.get("/tags")
async def list_tags(session: Session, page: Pagination) -> Page[TagOut]:
    query = select(Tag).order_by(Tag.id)
    return Page[TagOut].model_validate(await paginate(session, query, page))


@router.get("/places")
async def list_places(
    session: Session, page: Pagination, locality_id: int | None = None
) -> Page[PlaceOut]:
    query = select(Place).order_by(Place.id)
    if locality_id is not None:
        query = query.where(Place.locality_id == locality_id)
    return Page[PlaceOut].model_validate(await paginate(session, query, page))


@router.get("/places/{place_id}")
async def get_place(place_id: int, session: Session) -> PlaceOut:
    place = await session.get(Place, place_id)
    if place is None:
        raise not_found("Место не найдено")
    return PlaceOut.model_validate(place)
