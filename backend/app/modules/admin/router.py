from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BigId, SmallId, get_session
from app.core.security import CurrentUser, require_role
from app.modules.admin import service
from app.modules.admin.schemas import (
    PlaceCreate,
    PlaceOut,
    PlaceUpdate,
    RegionOut,
    SettingIn,
    SettingOut,
    TagIn,
    TagOut,
)
from app.modules.users.models import UserRole

Session = Annotated[AsyncSession, Depends(get_session)]
Admin = Annotated[CurrentUser, Depends(require_role(UserRole.ADMIN))]

# Публичное чтение справочников (без авторизации).
public_router = APIRouter(tags=["reference"])
# Управление справочниками и настройками — только admin (R-09, R-37, R-28).
admin_router = APIRouter(prefix="/admin", tags=["admin"])


@public_router.get("/regions")
async def list_regions(session: Session) -> list[RegionOut]:
    return [RegionOut.model_validate(r) for r in await service.list_regions(session)]


@public_router.get("/tags")
async def list_tags(session: Session) -> list[TagOut]:
    return [TagOut.model_validate(t) for t in await service.list_tags(session)]


@public_router.get("/places")
async def list_places(session: Session, region_id: SmallId | None = None) -> list[PlaceOut]:
    return [PlaceOut.model_validate(p) for p in await service.list_places(session, region_id)]


@public_router.get("/places/{place_id}")
async def get_place(place_id: BigId, session: Session) -> PlaceOut:
    return PlaceOut.model_validate(await service.get_place(session, place_id))


@admin_router.post("/tags", status_code=status.HTTP_201_CREATED)
async def create_tag(data: TagIn, _: Admin, session: Session) -> TagOut:
    return TagOut.model_validate(await service.create_tag(session, data.name))


@admin_router.patch("/tags/{tag_id}")
async def rename_tag(tag_id: SmallId, data: TagIn, _: Admin, session: Session) -> TagOut:
    return TagOut.model_validate(await service.rename_tag(session, tag_id, data.name))


@admin_router.delete("/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(tag_id: SmallId, _: Admin, session: Session) -> None:
    await service.delete_tag(session, tag_id)


@admin_router.post("/places", status_code=status.HTTP_201_CREATED)
async def create_place(data: PlaceCreate, _: Admin, session: Session) -> PlaceOut:
    return PlaceOut.model_validate(await service.create_place(session, data))


@admin_router.patch("/places/{place_id}")
async def update_place(place_id: BigId, data: PlaceUpdate, _: Admin, session: Session) -> PlaceOut:
    return PlaceOut.model_validate(await service.update_place(session, place_id, data))


@admin_router.delete("/places/{place_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_place(place_id: BigId, _: Admin, session: Session) -> None:
    await service.delete_place(session, place_id)


@admin_router.get("/settings")
async def list_settings(_: Admin, session: Session) -> list[SettingOut]:
    return [SettingOut.model_validate(s) for s in await service.list_settings(session)]


@admin_router.put("/settings/{key}")
async def update_setting(key: str, data: SettingIn, admin: Admin, session: Session) -> SettingOut:
    setting = await service.update_setting(session, actor_id=admin.id, key=key, value=data.value)
    return SettingOut.model_validate(setting)
