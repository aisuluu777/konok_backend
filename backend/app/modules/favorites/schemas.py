from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from app.core.db import BigId


class FavoriteCreate(BaseModel):
    host_id: BigId | None = None
    place_id: BigId | None = None
    vehicle_id: BigId | None = None

    @model_validator(mode="after")
    def exactly_one_target(self) -> Self:
        targets = [self.host_id, self.place_id, self.vehicle_id]
        if sum(t is not None for t in targets) != 1:  # R-38
            raise ValueError("Exactly one of host_id, place_id, vehicle_id is required")
        return self


class FavoriteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    host_id: int | None
    place_id: int | None
    vehicle_id: int | None
    created_at: datetime
