from typing import Annotated

from fastapi import APIRouter, Depends, Form, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BigId, get_session
from app.core.security import CurrentUser, get_current_user, require_role
from app.modules.kyc import service
from app.modules.kyc.models import ResidentType
from app.modules.kyc.schemas import KycOut, RejectIn
from app.modules.users.models import KycStatus

Session = Annotated[AsyncSession, Depends(get_session)]
Me = Annotated[CurrentUser, Depends(get_current_user)]
Reviewer = Annotated[CurrentUser, Depends(require_role(*service.REVIEWERS))]

router = APIRouter(prefix="/kyc", tags=["kyc"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def submit(
    passport: UploadFile,
    selfie: UploadFile,
    me: Me,
    session: Session,
    resident_type: Annotated[ResidentType | None, Form()] = None,
) -> KycOut:
    verification = await service.submit(session, me.id, passport.file, selfie.file, resident_type)
    return KycOut.model_validate(verification)


@router.get("/my")
async def list_my(me: Me, session: Session) -> list[KycOut]:
    return [KycOut.model_validate(v) for v in await service.list_own(session, me.id)]


@router.get("")
async def list_queue(
    _: Reviewer, session: Session, status: KycStatus | None = None
) -> list[KycOut]:
    return [KycOut.model_validate(v) for v in await service.list_queue(session, status)]


@router.get("/{kyc_id}/{document}")
async def get_document(
    kyc_id: BigId, document: service.Document, me: Me, session: Session
) -> FileResponse:
    path, media_type = await service.document_path(session, me, kyc_id, document)
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/{kyc_id}/approve")
async def approve(kyc_id: BigId, reviewer: Reviewer, session: Session) -> KycOut:
    verification = await service.decide(
        session, reviewer_id=reviewer.id, kyc_id=kyc_id, approve=True
    )
    return KycOut.model_validate(verification)


@router.post("/{kyc_id}/reject")
async def reject(kyc_id: BigId, data: RejectIn, reviewer: Reviewer, session: Session) -> KycOut:
    verification = await service.decide(
        session, reviewer_id=reviewer.id, kyc_id=kyc_id, approve=False, reason=data.reason
    )
    return KycOut.model_validate(verification)
