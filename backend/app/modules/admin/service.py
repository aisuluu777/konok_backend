from collections.abc import Callable, Sequence
from datetime import time
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import ConflictError, DomainError, NotFoundError
from app.modules.admin.models import Place, Region, Setting, Tag
from app.modules.admin.schemas import PlaceCreate, PlaceUpdate

# зачем нужен этот класс?
class SettingMissingError(Exception):
    """Ключа нет в settings — ошибка конфигурации (сид миграции), а не бизнес-ошибка."""


async def get_setting(session: AsyncSession, key: str) -> str:
    value = await session.scalar(select(Setting.value).where(Setting.key == key))
    if value is None:
        raise SettingMissingError(key)
    return value


async def get_int_setting(session: AsyncSession, key: str) -> int:
    return int(await get_setting(session, key))


async def _commit_unique(session: AsyncSession, message: str) -> None:
    """Коммит, где нарушение уникальности (name, slug) — это 409, остальное пробрасывается."""
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if getattr(exc.orig, "sqlstate", None) == "23505":  # unique_violation
            raise ConflictError(message) from exc
        raise


# --- Регионы (только чтение; ведутся сидом) --------------------------------


async def list_regions(session: AsyncSession) -> Sequence[Region]:
    return (await session.scalars(select(Region).order_by(Region.id))).all()


# --- Теги (R-09) -----------------------------------------------------------


async def list_tags(session: AsyncSession) -> Sequence[Tag]:
    return (await session.scalars(select(Tag).order_by(Tag.id))).all()


async def _get_tag(session: AsyncSession, tag_id: int) -> Tag:
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise NotFoundError("Tag not found")
    return tag


async def create_tag(session: AsyncSession, name: str) -> Tag:
    tag = Tag(name=name)
    session.add(tag)
    await _commit_unique(session, "Tag already exists")
    return tag


async def rename_tag(session: AsyncSession, tag_id: int, name: str) -> Tag:
    tag = await _get_tag(session, tag_id)
    tag.name = name
    await _commit_unique(session, "Tag already exists")
    return tag


async def delete_tag(session: AsyncSession, tag_id: int) -> None:
    await session.delete(await _get_tag(session, tag_id))
    await session.commit()


# --- Места (R-37) ----------------------------------------------------------


async def list_places(session: AsyncSession, region_id: int | None) -> Sequence[Place]:
    query = select(Place).order_by(Place.id)
    if region_id is not None:
        query = query.where(Place.region_id == region_id)
    return (await session.scalars(query)).all()


async def get_place(session: AsyncSession, place_id: int) -> Place:
    place = await session.get(Place, place_id)
    if place is None:
        raise NotFoundError("Place not found")
    return place


async def _check_region(session: AsyncSession, region_id: int) -> None:
    if await session.get(Region, region_id) is None:
        raise NotFoundError("Region not found")


async def create_place(session: AsyncSession, data: PlaceCreate) -> Place:
    await _check_region(session, data.region_id)
    place = Place(**data.model_dump())
    session.add(place)
    await _commit_unique(session, "Place slug already exists")
    await session.refresh(place)
    return place


async def update_place(session: AsyncSession, place_id: int, data: PlaceUpdate) -> Place:
    place = await get_place(session, place_id)
    # null в обязательных полях (slug, name, ...) = «не менять»; описания можно очистить null.
    changes = {
        field: value
        for field, value in data.model_dump(exclude_unset=True).items()
        if value is not None or field in {"short_description", "description", "cover_url"}
    }
    if "region_id" in changes:
        await _check_region(session, changes["region_id"])
    for field, value in changes.items():
        setattr(place, field, value)
    await _commit_unique(session, "Place slug already exists")
    await session.refresh(place)
    return place


async def delete_place(session: AsyncSession, place_id: int) -> None:
    await session.delete(await get_place(session, place_id))
    await session.commit()


# --- Настройки (R-28: изменение пишется в журнал) ---------------------------


async def list_settings(session: AsyncSession) -> Sequence[Setting]:
    return (await session.scalars(select(Setting).order_by(Setting.key))).all()


def _positive_int(value: str) -> bool:
    return value.isdigit() and int(value) > 0


def _rate(value: str) -> bool:
    # Как bookings.commission_rate: NUMERIC(5,4), 0 <= rate < 1.
    try:
        rate = Decimal(value)
        return Decimal("0") <= rate < Decimal("1") and rate == rate.quantize(Decimal("0.0001"))
    except ArithmeticError:
        return False


def _time(value: str) -> bool:
    try:
        time.fromisoformat(value)
    except ValueError:
        return False
    return True


# Формат значений известных ключей: мусор в настройке ломал бы auth и бронирование (500).
_SETTING_FORMATS: dict[str, Callable[[str], bool]] = {
    "access_token_ttl_min": _positive_int,
    "refresh_token_ttl_days": _positive_int,
    "otp_ttl_sec": _positive_int,
    "otp_resend_sec": _positive_int,
    "hold_minutes": _positive_int,
    "host_ack_reminder_hours": _positive_int,
    "checkin_time": _time,
    "checkout_time": _time,
    "commission_rate": _rate,
    "kyc_resubmit_hours": _positive_int,
}


async def update_setting(session: AsyncSession, *, actor_id: int, key: str, value: str) -> Setting:
    setting = await session.scalar(select(Setting).where(Setting.key == key).with_for_update())
    if setting is None:
        raise NotFoundError("Setting not found")
    is_valid = _SETTING_FORMATS.get(key)
    if is_valid is not None and not is_valid(value):
        raise DomainError(f"Invalid value for setting {key}")
    old_value = setting.value
    setting.value = value
    setting.updated_by = actor_id
    write_audit(
        session,
        actor_id=actor_id,
        action="setting_updated",
        entity_type="setting",
        entity_id=None,
        details={"key": key, "old_value": old_value, "new_value": value},
    )
    await session.commit()
    await session.refresh(setting)
    return setting
