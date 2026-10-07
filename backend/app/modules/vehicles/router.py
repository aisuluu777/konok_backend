from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BigId, SmallId, get_session
from app.core.security import CurrentUser, get_current_user
from app.modules.vehicles import service
from app.modules.vehicles.schemas import (
    VehicleCreate,
    VehicleOwnerOut,
    VehiclePublicOut,
    VehicleUpdate,
)

Session = Annotated[AsyncSession, Depends(get_session)]
Me = Annotated[CurrentUser, Depends(get_current_user)]

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_vehicle(data: VehicleCreate, me: Me, session: Session) -> VehicleOwnerOut:
    return VehicleOwnerOut.model_validate(await service.create_vehicle(session, me.id, data))


@router.get("")
async def list_catalog(
    session: Session, region_id: SmallId | None = None
) -> list[VehiclePublicOut]:
    vehicles = await service.list_catalog(session, region_id)
    return [VehiclePublicOut.model_validate(v) for v in vehicles]


@router.get("/my")
async def list_my_vehicles(me: Me, session: Session) -> list[VehicleOwnerOut]:
    vehicles = await service.list_own_vehicles(session, me.id)
    return [VehicleOwnerOut.model_validate(v) for v in vehicles]


@router.get("/{vehicle_id}")
async def get_vehicle(vehicle_id: BigId, session: Session) -> VehiclePublicOut:
    return VehiclePublicOut.model_validate(await service.get_public_vehicle(session, vehicle_id))


@router.patch("/{vehicle_id}")
async def update_vehicle(
    vehicle_id: BigId, data: VehicleUpdate, me: Me, session: Session
) -> VehicleOwnerOut:
    vehicle = await service.update_vehicle(session, me.id, vehicle_id, data)
    return VehicleOwnerOut.model_validate(vehicle)
