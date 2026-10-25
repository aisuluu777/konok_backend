from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import rules, storage
from app.deps import Session
from app.errors import bad_request, not_found
from app.models import Locality, Review, User, Vehicle, VehiclePhoto
from app.pagination import Page, Pagination, paginate
from app.schemas.bookings import ReviewCreate, ReviewOut
from app.schemas.vehicles import (
    DayAvailability,
    VehicleCreate,
    VehicleMe,
    VehiclePublic,
    VehicleUpdate,
)
from app.security import CurrentUser

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


async def _check_locality(session: AsyncSession, locality_id: int) -> None:
    if not await session.scalar(select(Locality.id).where(Locality.id == locality_id)):
        raise bad_request("Локация не найдена")


async def _my_vehicle(session: AsyncSession, me: User, vehicle_id: int) -> Vehicle:
    vehicle = await session.get(Vehicle, vehicle_id)
    if vehicle is None or vehicle.owner_id != me.id:
        raise not_found("Авто не найдено")
    return vehicle


async def _visible_vehicle(session: AsyncSession, vehicle_id: int) -> tuple[Vehicle, User]:
    row = (
        await session.execute(
            select(Vehicle, User)
            .join(User, User.id == Vehicle.owner_id)
            .where(Vehicle.id == vehicle_id, rules.vehicle_visible())
        )
    ).first()
    if row is None:
        raise not_found("Авто не найдено")
    return row[0], row[1]


async def _public(session: AsyncSession, vehicle: Vehicle, owner: User) -> VehiclePublic:
    rating, reviews_count = await rules.vehicle_rating(session, vehicle.id)
    return VehiclePublic.model_validate(
        {
            **{f: getattr(vehicle, f) for f in VehiclePublic.model_fields if hasattr(vehicle, f)},
            "photos": vehicle.photos,
            "owner_name": owner.full_name,
            "rating": rating,
            "reviews_count": reviews_count,
        }
    )


# --- Свои авто -------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_vehicle(data: VehicleCreate, me: CurrentUser, session: Session) -> VehicleMe:
    """В каталоге авто появится, когда admin одобрит документы владельца (is_verified)."""
    await _check_locality(session, data.locality_id)
    vehicle = Vehicle(owner_id=me.id, **data.model_dump())
    session.add(vehicle)
    await session.commit()
    await session.refresh(vehicle, ["photos"])
    return VehicleMe.model_validate(vehicle)


@router.get("/my")
async def list_my_vehicles(me: CurrentUser, session: Session, page: Pagination) -> Page[VehicleMe]:
    query = select(Vehicle).where(Vehicle.owner_id == me.id).order_by(Vehicle.id)
    return Page[VehicleMe].model_validate(await paginate(session, query, page))


@router.patch("/{vehicle_id}")
async def update_vehicle(
    vehicle_id: int, data: VehicleUpdate, me: CurrentUser, session: Session
) -> VehicleMe:
    vehicle = await _my_vehicle(session, me, vehicle_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("locality_id") is not None:
        await _check_locality(session, changes["locality_id"])
    for field, value in changes.items():
        if value is None and field in {"locality_id", "title", "price_per_day", "is_published"}:
            continue
        setattr(vehicle, field, value)
    await session.commit()
    return VehicleMe.model_validate(vehicle)


@router.post("/{vehicle_id}/photos", status_code=status.HTTP_201_CREATED)
async def add_vehicle_photo(
    vehicle_id: int, file: UploadFile, me: CurrentUser, session: Session
) -> VehicleMe:
    vehicle = await _my_vehicle(session, me, vehicle_id)
    vehicle.photos.append(VehiclePhoto(url=await storage.save_photo(file)))
    await session.commit()
    return VehicleMe.model_validate(vehicle)


@router.delete("/{vehicle_id}/photos/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vehicle_photo(
    vehicle_id: int, photo_id: int, me: CurrentUser, session: Session
) -> None:
    await _my_vehicle(session, me, vehicle_id)
    photo = await session.get(VehiclePhoto, photo_id)
    if photo is None or photo.vehicle_id != vehicle_id:
        raise not_found("Фото не найдено")
    storage.delete_photo(photo.url)
    await session.delete(photo)
    await session.commit()


# --- Каталог -------------------------------------------------------------------


@router.get("")
async def list_vehicles(
    session: Session, page: Pagination, locality_id: int | None = None
) -> Page[VehiclePublic]:
    """Авто владельцев с одобренными документами."""
    query = (
        select(Vehicle, User).join(User, User.id == Vehicle.owner_id).where(rules.vehicle_visible())
    )
    if locality_id is not None:
        query = query.where(Vehicle.locality_id == locality_id)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = await session.execute(
        query.order_by(Vehicle.created_at.desc()).limit(page.limit).offset(page.offset)
    )
    return Page[VehiclePublic].model_validate(
        {
            "items": [await _public(session, vehicle, owner) for vehicle, owner in rows],
            "total": total,
            "limit": page.limit,
            "offset": page.offset,
        }
    )


@router.get("/{vehicle_id}")
async def get_vehicle(vehicle_id: int, session: Session) -> VehiclePublic:
    vehicle, owner = await _visible_vehicle(session, vehicle_id)
    return await _public(session, vehicle, owner)


@router.get("/{vehicle_id}/availability")
async def get_vehicle_availability(
    vehicle_id: int,
    session: Session,
    start_date: Annotated[date, Query(description="Первый день")],
    end_date: Annotated[date, Query(description="День после последнего")],
) -> list[DayAvailability]:
    if end_date <= start_date or (end_date - start_date).days > 366:
        raise bad_request("Период: end_date позже start_date, не больше 366 дней")
    await _visible_vehicle(session, vehicle_id)
    busy = await rules.busy_vehicle_days(session, vehicle_id, start_date, end_date)
    return [
        DayAvailability(date=day, is_free=day not in busy)
        for day in rules.days(start_date, end_date)
    ]


@router.get("/{vehicle_id}/reviews")
async def list_vehicle_reviews(
    vehicle_id: int, session: Session, page: Pagination
) -> Page[ReviewOut]:
    await _visible_vehicle(session, vehicle_id)
    query = select(Review).where(Review.vehicle_id == vehicle_id).order_by(Review.id.desc())
    return Page[ReviewOut].model_validate(await paginate(session, query, page))


@router.post("/{vehicle_id}/reviews", status_code=status.HTTP_201_CREATED)
async def create_vehicle_review(
    vehicle_id: int, data: ReviewCreate, me: CurrentUser, session: Session
) -> ReviewOut:
    """Отзыв об авто может оставить любой пользователь, кроме владельца."""
    vehicle, _ = await _visible_vehicle(session, vehicle_id)
    if vehicle.owner_id == me.id:
        raise bad_request("Нельзя оставить отзыв о своём авто")
    review = Review(author_id=me.id, vehicle_id=vehicle_id, rating=data.rating)
    setattr(review, f"text_{data.language}", data.text)
    session.add(review)
    await session.commit()
    await session.refresh(review)
    return ReviewOut.model_validate(review)
