from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BigId, get_session
from app.core.security import CurrentUser, get_current_user, require_role
from app.modules.bookings import service
from app.modules.bookings.models import BookingStatus
from app.modules.bookings.schemas import (
    AvailabilityQuery,
    BookingOut,
    CancelIn,
    CheckInIn,
    NightAvailability,
    StayBookingCreate,
    VehicleBookingCreate,
)
from app.modules.payments.provider import PaymentProvider, get_payment_provider
from app.modules.users.models import UserRole

Session = Annotated[AsyncSession, Depends(get_session)]
Me = Annotated[CurrentUser, Depends(get_current_user)]

Staff = Annotated[CurrentUser, Depends(require_role(UserRole.MODERATOR, UserRole.ADMIN))]
Provider = Annotated[PaymentProvider, Depends(get_payment_provider)]

router = APIRouter(tags=["bookings"])


@router.post("/bookings/stay", status_code=status.HTTP_201_CREATED)
async def create_stay(data: StayBookingCreate, me: Me, session: Session) -> BookingOut:
    return BookingOut.model_validate(await service.create_stay(session, me.id, data))


@router.post("/bookings/vehicle", status_code=status.HTTP_201_CREATED)
async def create_vehicle_booking(
    data: VehicleBookingCreate, me: Me, session: Session
) -> BookingOut:
    return BookingOut.model_validate(await service.create_vehicle_booking(session, me.id, data))


@router.get("/bookings/{booking_id}")
async def get_booking(booking_id: BigId, me: Me, session: Session) -> BookingOut:
    booking, address = await service.get_booking(session, me.id, booking_id)
    return BookingOut.model_validate(booking).model_copy(update={"host_address": address})


@router.post("/bookings/{booking_id}/checkin")
async def check_in(booking_id: BigId, data: CheckInIn, me: Me, session: Session) -> BookingOut:
    return BookingOut.model_validate(await service.check_in(session, me.id, booking_id, data.pin))


@router.post("/bookings/{booking_id}/cancel")
async def cancel_booking(
    booking_id: BigId, data: CancelIn, me: Me, session: Session, provider: Provider
) -> BookingOut:
    booking = await service.cancel_booking(session, provider, me.id, booking_id, data.reason)
    return BookingOut.model_validate(booking)


@router.post("/bookings/{booking_id}/complete")
async def complete_booking(booking_id: BigId, _: Staff, session: Session) -> BookingOut:
    booking = await service.close_booking(session, booking_id, BookingStatus.COMPLETED)
    return BookingOut.model_validate(booking)


@router.post("/bookings/{booking_id}/no-show")
async def no_show_booking(booking_id: BigId, _: Staff, session: Session) -> BookingOut:
    booking = await service.close_booking(session, booking_id, BookingStatus.NO_SHOW)
    return BookingOut.model_validate(booking)


@router.get("/hosts/{host_id}/availability")
async def get_availability(
    host_id: BigId, period: Annotated[AvailabilityQuery, Query()], session: Session
) -> list[NightAvailability]:
    nights = await service.get_availability(session, host_id, period.start_date, period.end_date)
    return [NightAvailability.model_validate(n) for n in nights]
