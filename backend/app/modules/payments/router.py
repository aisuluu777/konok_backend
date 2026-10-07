from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BigId, get_session
from app.core.security import CurrentUser, get_current_user
from app.modules.payments import service
from app.modules.payments.provider import PaymentProvider, get_payment_provider
from app.modules.payments.schemas import AddActivitiesIn, PayIn, PaymentOut, PayOut

Session = Annotated[AsyncSession, Depends(get_session)]
Me = Annotated[CurrentUser, Depends(get_current_user)]
Provider = Annotated[PaymentProvider, Depends(get_payment_provider)]

router = APIRouter(tags=["payments"])


@router.post("/bookings/{booking_id}/pay")
async def pay_booking(
    booking_id: BigId, data: PayIn, me: Me, session: Session, provider: Provider
) -> PayOut:
    payment, booking = await service.pay_booking(
        session, provider, payer_id=me.id, booking_id=booking_id, data=data
    )
    return PayOut(payment=PaymentOut.model_validate(payment), booking_status=booking.status)


@router.post("/bookings/{booking_id}/activities")
async def add_activities(
    booking_id: BigId, data: AddActivitiesIn, me: Me, session: Session, provider: Provider
) -> PayOut:
    payment, booking = await service.buy_activities(
        session, provider, payer_id=me.id, booking_id=booking_id, data=data
    )
    return PayOut(payment=PaymentOut.model_validate(payment), booking_status=booking.status)
