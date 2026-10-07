from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.modules.reviews.models import ReviewStatus


class ReviewCreate(BaseModel):
    rating: Annotated[int, Field(ge=1, le=5)]
    text: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
        | None
    ) = None


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_id: int
    author_id: int
    target_user_id: int
    rating: int
    text: str | None
    status: ReviewStatus
    created_at: datetime
