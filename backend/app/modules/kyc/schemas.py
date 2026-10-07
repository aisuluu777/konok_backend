from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.modules.kyc.models import ResidentType
from app.modules.users.models import KycStatus


class KycOut(BaseModel):
    """Без файлов документов: они отдаются только отдельными защищёнными маршрутами (R-04)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    status: KycStatus
    resident_type: ResidentType | None
    rejection_reason: str | None
    submitted_at: datetime
    checked_at: datetime | None


class RejectIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
