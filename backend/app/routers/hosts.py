from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Query, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import rules, storage
from app.deps import Session
from app.errors import bad_request, conflict, not_found
from app.models import (
    Activity,
    HostBlockedDate,
    HostPhoto,
    HostProfile,
    Locality,
    Review,
    User,
)
from app.pagination import Page, Pagination, paginate
from app.schemas.activities import ActivityOut
from app.schemas.bookings import ReviewCreate, ReviewOut
from app.schemas.hosts import (
    BlockedDatesIn,
    BlockedDatesOut,
    HostCreate,
    HostMe,
    HostPublic,
    HostUpdate,
    NightAvailability,
)
from app.security import CurrentUser

router = APIRouter(prefix="/hosts", tags=["hosts"])

MAX_CALENDAR_DAYS = 366


async def _my_profile(session: AsyncSession, user: User, lock: bool = False) -> HostProfile:
    query = select(HostProfile).where(HostProfile.user_id == user.id)
    if lock:
        query = query.with_for_update()
    host = await session.scalar(query)
    if host is None:
        raise not_found("У вас нет профиля хозяина")
    return host


async def _check_locality(session: AsyncSession, locality_id: int) -> None:
    if not await session.scalar(select(Locality.id).where(Locality.id == locality_id)):
        raise bad_request("Локация не найдена")


async def _visible_host(session: AsyncSession, host_id: int) -> tuple[HostProfile, User]:
    row = (
        await session.execute(
            select(HostProfile, User)
            .join(User, User.id == HostProfile.user_id)
            .where(HostProfile.user_id == host_id, rules.host_visible())
        )
    ).first()
    if row is None:
        raise not_found("Хозяин не найден")
    return row[0], row[1]


async def host_public(session: AsyncSession, host: HostProfile, user: User) -> HostPublic:
    rating, reviews_count = await rules.host_rating(session, host.user_id)
    return HostPublic.model_validate(
        {
            **{f: getattr(host, f) for f in HostPublic.model_fields if hasattr(host, f)},
            "photos": host.photos,
            "full_name": user.full_name,
            "rating": rating,
            "reviews_count": reviews_count,
            "on_platform_since": user.verified_at,
        }
    )


def _check_period(start: date, end: date) -> None:
    if end <= start or (end - start).days > MAX_CALENDAR_DAYS:
        raise bad_request(f"Период: end_date позже start_date, не больше {MAX_CALENDAR_DAYS} дней")


# --- Свой профиль -----------------------------------------------------------


@router.post("/me", status_code=status.HTTP_201_CREATED)
async def create_my_profile(data: HostCreate, me: CurrentUser, session: Session) -> HostMe:
    """Создаёт профиль хозяина целиком (черновиков нет). Публикуется сразу.

    В каталоге хозяин появится, когда admin одобрит документы (is_verified).
    """
    if await session.get(HostProfile, me.id):
        raise conflict("Профиль хозяина уже создан")
    await _check_locality(session, data.locality_id)
    host = HostProfile(user_id=me.id, **data.model_dump())
    session.add(host)
    await session.commit()
    await session.refresh(host, ["photos"])
    return HostMe.model_validate(host)


@router.get("/me")
async def get_my_profile(me: CurrentUser, session: Session) -> HostMe:
    return HostMe.model_validate(await _my_profile(session, me))


@router.patch("/me")
async def update_my_profile(data: HostUpdate, me: CurrentUser, session: Session) -> HostMe:
    host = await _my_profile(session, me)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("locality_id") is not None:
        await _check_locality(session, changes["locality_id"])
    for field, value in changes.items():
        if value is None and field in {"locality_id", "address", "capacity", "price_per_person"}:
            continue  # обязательные поля null не очищает
        setattr(host, field, value)
    await session.commit()
    return HostMe.model_validate(host)


@router.post("/me/photos", status_code=status.HTTP_201_CREATED)
async def add_my_photo(file: UploadFile, me: CurrentUser, session: Session) -> HostMe:
    """Фото жилья: JPEG / PNG / WebP до 10 МБ."""
    host = await _my_profile(session, me)
    host.photos.append(HostPhoto(url=await storage.save_photo(file)))
    await session.commit()
    return HostMe.model_validate(host)


@router.delete("/me/photos/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_photo(photo_id: int, me: CurrentUser, session: Session) -> None:
    photo = await session.get(HostPhoto, photo_id)
    if photo is None or photo.host_id != me.id:
        raise not_found("Фото не найдено")
    storage.delete_photo(photo.url)
    await session.delete(photo)
    await session.commit()


# --- Календарь хозяина -----------------------------------------------------------


async def _future_blocked(session: AsyncSession, host_id: int) -> BlockedDatesOut:
    rows = await session.scalars(
        select(HostBlockedDate.date)
        .where(HostBlockedDate.host_id == host_id, HostBlockedDate.date >= rules.today_bishkek())
        .order_by(HostBlockedDate.date)
    )
    return BlockedDatesOut(dates=list(rows))


@router.get("/me/blocked-dates")
async def get_blocked_dates(me: CurrentUser, session: Session) -> BlockedDatesOut:
    """Закрытые даты начиная с сегодняшней."""
    await _my_profile(session, me)
    return await _future_blocked(session, me.id)


@router.post("/me/blocked-dates")
async def block_dates(data: BlockedDatesIn, me: CurrentUser, session: Session) -> BlockedDatesOut:
    """Закрыть даты. Можно только свободные: без оплаченных броней на эту ночь."""
    host = await _my_profile(session, me, lock=True)  # параллельная оплата брони ждёт
    for day in data.dates:
        if day < rules.today_bishkek():
            raise bad_request(f"{day} — дата в прошлом")
        seats = await rules.free_seats(session, host, day, day + timedelta(days=1))
        if seats[day] < host.capacity:
            raise conflict(f"На {day} уже есть брони — закрыть нельзя")
    await session.execute(
        insert(HostBlockedDate)
        .values([{"host_id": me.id, "date": day} for day in set(data.dates)])
        .on_conflict_do_nothing()
    )
    await session.commit()
    return await _future_blocked(session, me.id)


@router.delete("/me/blocked-dates/{day}")
async def unblock_date(day: date, me: CurrentUser, session: Session) -> BlockedDatesOut:
    await _my_profile(session, me)
    await session.execute(
        delete(HostBlockedDate).where(HostBlockedDate.host_id == me.id, HostBlockedDate.date == day)
    )
    await session.commit()
    return await _future_blocked(session, me.id)


# --- Каталог -------------------------------------------------------------------


@router.get("")
async def list_hosts(
    session: Session, page: Pagination, locality_id: int | None = None
) -> Page[HostPublic]:
    """Хозяева с одобренными документами."""
    query = (
        select(HostProfile, User)
        .join(User, User.id == HostProfile.user_id)
        .where(rules.host_visible())
    )
    if locality_id is not None:
        query = query.where(HostProfile.locality_id == locality_id)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = await session.execute(
        query.order_by(HostProfile.created_at.desc()).limit(page.limit).offset(page.offset)
    )
    result = {
        "items": [await host_public(session, host, user) for host, user in rows],
        "total": total,
        "limit": page.limit,
        "offset": page.offset,
    }
    return Page[HostPublic].model_validate(result)


@router.get("/{host_id}")
async def get_host(host_id: int, session: Session) -> HostPublic:
    host, user = await _visible_host(session, host_id)
    return await host_public(session, host, user)


@router.get("/{host_id}/availability")
async def get_availability(
    host_id: int,
    session: Session,
    start_date: Annotated[date, Query(description="Первая ночь")],
    end_date: Annotated[date, Query(description="День после последней ночи")],
) -> list[NightAvailability]:
    """Свободные места на каждую ночь периода."""
    _check_period(start_date, end_date)
    host, _ = await _visible_host(session, host_id)
    seats = await rules.free_seats(session, host, start_date, end_date)
    blocked = await rules.blocked_dates(session, host_id, start_date, end_date)
    return [
        NightAvailability(date=night, free_seats=max(free, 0), is_blocked=night in blocked)
        for night, free in seats.items()
    ]


@router.get("/{host_id}/activities")
async def list_host_activities(
    host_id: int, session: Session, page: Pagination
) -> Page[ActivityOut]:
    await _visible_host(session, host_id)
    query = (
        select(Activity)
        .where(Activity.host_id == host_id, Activity.is_active)
        .order_by(Activity.id)
    )
    return Page[ActivityOut].model_validate(await paginate(session, query, page))


@router.get("/{host_id}/reviews")
async def list_host_reviews(host_id: int, session: Session, page: Pagination) -> Page[ReviewOut]:
    await _visible_host(session, host_id)
    query = select(Review).where(Review.host_id == host_id).order_by(Review.id.desc())
    return Page[ReviewOut].model_validate(await paginate(session, query, page))


@router.post("/{host_id}/reviews", status_code=status.HTTP_201_CREATED)
async def create_host_review(
    host_id: int, data: ReviewCreate, me: CurrentUser, session: Session
) -> ReviewOut:
    """Отзыв о хозяине может оставить любой пользователь, кроме самого хозяина."""
    await _visible_host(session, host_id)
    if host_id == me.id:
        raise bad_request("Нельзя оставить отзыв о себе")
    review = Review(author_id=me.id, host_id=host_id, rating=data.rating)
    setattr(review, f"text_{data.language}", data.text)
    session.add(review)
    await session.commit()
    await session.refresh(review)
    return ReviewOut.model_validate(review)
