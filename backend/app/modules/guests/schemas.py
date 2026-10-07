from datetime import date, datetime
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, Field, StringConstraints

from app.core.clock import today_bishkek
from app.core.storage import IMAGE_URL_PATTERN
from app.modules.guests.models import GuestProfile

Country = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Interest = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
# Только наш загруженный файл-фото (Step 4), не произвольный внешний URL.
AvatarUrl = Annotated[str, StringConstraints(pattern=IMAGE_URL_PATTERN)]


def age_on(birth_date: date, today: date) -> int:
    """Полных лет на дату today (родившиеся 29 февраля стареют 1 марта в невисокосный год)."""
    before_birthday = (today.month, today.day) < (birth_date.month, birth_date.day)
    return today.year - birth_date.year - before_birthday


def _not_in_future(value: date) -> date:
    if value > today_bishkek():
        raise ValueError("birth_date cannot be in the future")
    return value


class GuestProfileIn(BaseModel):
    country: Country | None = None
    birth_date: Annotated[date, AfterValidator(_not_in_future)] | None = None
    avatar_url: AvatarUrl | None = None
    interests: Annotated[list[Interest], Field(max_length=20)] = []


class GuestPublicOut(BaseModel):
    """Профиль гостя для хозяина: возраст вместо даты рождения (schema.sql)."""

    user_id: int
    country: str | None
    age: int | None
    avatar_url: str | None
    interests: list[str]

    @classmethod
    def from_profile(cls, profile: GuestProfile) -> Self:
        return cls(
            user_id=profile.user_id,
            country=profile.country,
            age=age_on(profile.birth_date, today_bishkek()) if profile.birth_date else None,
            avatar_url=profile.avatar_url,
            interests=profile.interests,
        )


class GuestProfileOut(GuestPublicOut):
    """Владельцу — всё, включая дату рождения."""

    birth_date: date | None
    updated_at: datetime

    @classmethod
    def from_profile(cls, profile: GuestProfile) -> Self:
        public = GuestPublicOut.from_profile(profile).model_dump()
        return cls(**public, birth_date=profile.birth_date, updated_at=profile.updated_at)
