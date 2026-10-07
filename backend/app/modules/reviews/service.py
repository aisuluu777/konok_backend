from sqlalchemy import ColumnElement, and_, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.bookings.models import Booking, BookingStatus, BookingType
from app.modules.hosts.models import HostProfile
from app.modules.notifications.service import create_notification
from app.modules.payments.service import provider_id
from app.modules.reviews.models import Review, ReviewStatus
from app.modules.reviews.schemas import ReviewCreate
from app.modules.vehicles.models import Vehicle


async def _refresh_aggregate(session: AsyncSession, booking: Booking) -> None:
    """Пересчитывает rating_avg / reviews_count хозяина или машины по опубликованным отзывам.

    Строка цели блокируется: два отзыва одной цели не затрут пересчёт друг друга.
    """
    target: type[HostProfile] | type[Vehicle]
    if booking.type == BookingType.STAY:
        target = HostProfile
        owner: ColumnElement[bool] = HostProfile.user_id == booking.host_id
        in_scope: ColumnElement[bool] = and_(
            Booking.type == BookingType.STAY, Booking.host_id == booking.host_id
        )
    else:
        target = Vehicle
        owner = Vehicle.id == booking.vehicle_id
        in_scope = Booking.vehicle_id == booking.vehicle_id
    await session.execute(select(target).where(owner).with_for_update())
    avg, count = (
        await session.execute(
            select(func.round(func.avg(Review.rating), 2), func.count())
            .join(Booking, Booking.id == Review.booking_id)
            .where(in_scope, Review.status == ReviewStatus.PUBLISHED)
        )
    ).one()
    await session.execute(
        update(target).where(owner).values(rating_avg=avg or 0, reviews_count=count)
    )


async def create_review(
    session: AsyncSession, author_id: int, booking_id: int, data: ReviewCreate
) -> Review:
    """Отзыв гостя на свою completed-бронь, один на бронь (R-26); цель берётся из брони."""
    booking = await session.scalar(
        select(Booking).where(Booking.id == booking_id).with_for_update()
    )
    if booking is None or booking.guest_id != author_id:
        raise NotFoundError("Booking not found")
    if booking.status != BookingStatus.COMPLETED:
        raise ConflictError("A review can be left only after the booking is completed")
    if await session.scalar(
        select(Review.id).where(Review.booking_id == booking.id, Review.author_id == author_id)
    ):
        raise ConflictError("Review for this booking already exists")

    target_id = await provider_id(session, booking)
    review = Review(
        booking_id=booking.id,
        author_id=author_id,
        target_user_id=target_id,
        rating=data.rating,
        text=data.text,
    )
    session.add(review)
    try:
        await session.flush()
    except IntegrityError as exc:  # страховка: unique (booking_id, author_id)
        await session.rollback()
        raise ConflictError("Review for this booking already exists") from exc
    await _refresh_aggregate(session, booking)
    create_notification(  # R-39
        session,
        user_id=target_id,
        type="review_received",
        payload={"booking_id": booking.id, "review_id": review.id, "rating": review.rating},
    )
    await session.commit()
    await session.refresh(review)
    return review
