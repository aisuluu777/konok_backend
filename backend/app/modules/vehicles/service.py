from collections.abc import Sequence

from sqlalchemy import ColumnElement, Select, and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.admin.models import Region
from app.modules.users.models import KycStatus, User, UserStatus
from app.modules.vehicles.models import Vehicle
from app.modules.vehicles.schemas import VehicleCreate, VehicleUpdate

# Поля, которые можно очистить null; в остальных null = «не менять».
_NULLABLE = {"year", "seats", "transmission", "fuel"}


async def _check_region(session: AsyncSession, region_id: int) -> None:
    if await session.get(Region, region_id) is None:
        raise NotFoundError("Region not found")


def _visible() -> ColumnElement[bool]:
    """Опубликована, KYC владельца одобрен, владелец не заблокирован (R-03)."""
    return and_(
        Vehicle.is_published.is_(True),
        User.kyc_status == KycStatus.APPROVED,
        User.status == UserStatus.ACTIVE,
    )


def _public_query() -> Select[Vehicle]:
    return select(Vehicle).join(User, User.id == Vehicle.owner_id).where(_visible())


async def _own_vehicle(session: AsyncSession, user_id: int, vehicle_id: int) -> Vehicle:
    """Чужая машина — 404, как и несуществующая."""
    vehicle = await session.get(Vehicle, vehicle_id)
    if vehicle is None or vehicle.owner_id != user_id:
        raise NotFoundError("Vehicle not found")
    return vehicle


async def create_vehicle(session: AsyncSession, user_id: int, data: VehicleCreate) -> Vehicle:
    """Машину добавляет любой user и становится её владельцем (R-02)."""
    await _check_region(session, data.region_id)
    vehicle = Vehicle(owner_id=user_id, **data.model_dump())
    session.add(vehicle)
    await session.commit()
    await session.refresh(vehicle)
    return vehicle


async def update_vehicle(
    session: AsyncSession, user_id: int, vehicle_id: int, data: VehicleUpdate
) -> Vehicle:
    """Цена копируется в бронь (R-17), поэтому её можно менять. Снять с публикации — R-40."""
    vehicle = await _own_vehicle(session, user_id, vehicle_id)
    changes = {
        field: value
        for field, value in data.model_dump(exclude_unset=True).items()
        if value is not None or field in _NULLABLE
    }
    if "region_id" in changes:
        await _check_region(session, changes["region_id"])
    for field, value in changes.items():
        setattr(vehicle, field, value)
    await session.commit()
    await session.refresh(vehicle)
    return vehicle


async def list_own_vehicles(session: AsyncSession, user_id: int) -> Sequence[Vehicle]:
    """Свои машины, включая неопубликованные."""
    query = select(Vehicle).where(Vehicle.owner_id == user_id).order_by(Vehicle.id)
    return (await session.scalars(query)).all()


async def list_catalog(session: AsyncSession, region_id: int | None) -> Sequence[Vehicle]:
    query = _public_query().order_by(Vehicle.id)
    if region_id is not None:
        query = query.where(Vehicle.region_id == region_id)
    return (await session.scalars(query)).all()


async def get_public_vehicle(session: AsyncSession, vehicle_id: int) -> Vehicle:
    vehicle = await session.scalar(_public_query().where(Vehicle.id == vehicle_id))
    if vehicle is None:
        raise NotFoundError("Vehicle not found")
    return vehicle
