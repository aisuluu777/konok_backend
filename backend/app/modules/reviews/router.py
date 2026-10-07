from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BigId, get_session
from app.core.security import CurrentUser, get_current_user
from app.modules.reviews import service
from app.modules.reviews.schemas import ReviewCreate, ReviewOut

Session = Annotated[AsyncSession, Depends(get_session)]
Me = Annotated[CurrentUser, Depends(get_current_user)]

router = APIRouter(tags=["reviews"])


@router.post("/bookings/{booking_id}/reviews", status_code=status.HTTP_201_CREATED)
async def create_review(
    booking_id: BigId, data: ReviewCreate, me: Me, session: Session
) -> ReviewOut:
    return ReviewOut.model_validate(await service.create_review(session, me.id, booking_id, data))
