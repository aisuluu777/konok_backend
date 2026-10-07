"""Профиль гостя: GET/PUT /guests/me, возраст по Азии/Бишкеку, публичный вид (Step 6 plan).

Хозяин видит только возраст гостя, дата рождения наружу не отдаётся (GuestPublicOut).
"""

import uuid
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.guests.models import GuestProfile
from app.modules.guests.schemas import GuestPublicOut, age_on
from app.modules.users.models import User
from tests.admin.helpers import assert_error
from tests.factories import auth, create_user, login

BISHKEK = ZoneInfo("Asia/Bishkek")
PROFILE_FIELDS = {
    "user_id",
    "country",
    "birth_date",
    "age",
    "avatar_url",
    "interests",
    "updated_at",
}
PUBLIC_FIELDS = {"user_id", "country", "age", "avatar_url", "interests"}


def bishkek_today() -> date:
    return datetime.now(BISHKEK).date()


def years_before(day: date, years: int) -> date:
    """Та же дата `years` лет назад; 29 февраля в невисокосный год → 28 февраля."""
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def avatar(ext: str = "jpg") -> str:
    return f"/files/{uuid.uuid4().hex}.{ext}"


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def user_with_headers(
    db_session: AsyncSession, client: AsyncClient
) -> tuple[User, dict[str, str]]:
    user = await create_user(db_session)
    tokens = await login(client, user.email)
    return user, auth(tokens["access_token"])


async def put_profile(
    client: AsyncClient, headers: dict[str, str], body: dict[str, Any]
) -> dict[str, Any]:
    response = await client.put("/guests/me", headers=headers, json=body)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def profile_rows(db_session: AsyncSession, user_id: int | None = None) -> int:
    query = select(func.count()).select_from(GuestProfile)
    if user_id is not None:
        query = query.where(GuestProfile.user_id == user_id)
    count: int = await db_session.scalar(query) or 0
    return count


# --- без токена ---


@pytest.mark.parametrize("method", ["GET", "PUT"])
async def test_guest_me_requires_auth(client: AsyncClient, method: str) -> None:
    """Step 6 plan: /guests/me без токена → 401 unauthorized."""
    kwargs: dict[str, Any] = {"json": {}} if method == "PUT" else {}
    response = await client.request(method, "/guests/me", **kwargs)

    assert_error(response, 401, "unauthorized")


# --- GET /guests/me ---


async def test_get_without_profile_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 6 plan (граница): профиль ещё не создан → 404 not_found."""
    _, headers = await user_with_headers(db_session, client)

    response = await client.get("/guests/me", headers=headers)

    assert_error(response, 404, "not_found")


async def test_get_does_not_return_other_users_profile(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 6 plan (нарушение): профиль другого пользователя по GET /guests/me не виден."""
    _, other_headers = await user_with_headers(db_session, client)
    await put_profile(client, other_headers, {"country": "Казахстан"})
    _, headers = await user_with_headers(db_session, client)

    response = await client.get("/guests/me", headers=headers)

    assert_error(response, 404, "not_found")


async def test_get_returns_saved_profile(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 6 plan: GET после PUT возвращает тот же GuestProfileOut."""
    _, headers = await user_with_headers(db_session, client)
    saved = await put_profile(
        client,
        headers,
        {"country": "Германия", "birth_date": "1990-05-01", "interests": ["горы"]},
    )

    response = await client.get("/guests/me", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == saved


# --- PUT /guests/me: создание и замена ---


async def test_put_creates_profile_with_full_shape(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 6 plan: PUT создаёт профиль → 200 GuestProfileOut со всеми полями, строка в БД."""
    user, headers = await user_with_headers(db_session, client)
    today = bishkek_today()
    birth = years_before(today, 30) - timedelta(days=10)
    url = avatar("png")

    data = await put_profile(
        client,
        headers,
        {
            "country": "Германия",
            "birth_date": birth.isoformat(),
            "avatar_url": url,
            "interests": ["горы", "кухня", "кони"],
        },
    )

    assert set(data) == PROFILE_FIELDS
    assert data["user_id"] == user.id
    assert data["country"] == "Германия"
    assert data["birth_date"] == birth.isoformat()
    assert data["age"] == 30
    assert data["avatar_url"] == url
    assert data["interests"] == ["горы", "кухня", "кони"]
    assert parse_ts(data["updated_at"]).tzinfo is not None

    row = await db_session.get(GuestProfile, user.id)
    assert row is not None
    assert row.country == "Германия"
    assert row.birth_date == birth
    assert row.avatar_url == url
    assert list(row.interests) == ["горы", "кухня", "кони"]


async def test_put_empty_body_creates_empty_profile(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 6 plan (граница): PUT {} → профиль с null-полями и пустыми интересами, age null."""
    user, headers = await user_with_headers(db_session, client)

    data = await put_profile(client, headers, {})

    assert data["user_id"] == user.id
    assert data["country"] is None
    assert data["birth_date"] is None
    assert data["age"] is None
    assert data["avatar_url"] is None
    assert data["interests"] == []
    assert await profile_rows(db_session, user.id) == 1


async def test_put_explicit_nulls_accepted(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 6 plan: необязательные поля можно передать явным null."""
    _, headers = await user_with_headers(db_session, client)

    data = await put_profile(
        client, headers, {"country": None, "birth_date": None, "avatar_url": None}
    )

    assert data["country"] is None
    assert data["birth_date"] is None
    assert data["age"] is None
    assert data["avatar_url"] is None
    assert data["interests"] == []


async def test_put_is_full_replace(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 6 plan: повторный PUT — полная замена: отсутствующие поля сбрасываются в null / []."""
    user, headers = await user_with_headers(db_session, client)
    await put_profile(
        client,
        headers,
        {
            "country": "Германия",
            "birth_date": "1990-05-01",
            "avatar_url": avatar(),
            "interests": ["горы"],
        },
    )

    data = await put_profile(client, headers, {"country": "Франция"})

    assert data["country"] == "Франция"
    assert data["birth_date"] is None
    assert data["age"] is None
    assert data["avatar_url"] is None
    assert data["interests"] == []
    got = await client.get("/guests/me", headers=headers)
    assert got.json() == data
    row = await db_session.get(GuestProfile, user.id)
    assert row is not None
    assert row.birth_date is None
    assert row.avatar_url is None
    assert list(row.interests) == []


async def test_repeated_put_keeps_single_row(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 6 plan: повторный PUT меняет ту же строку — в БД одна строка на пользователя,
    updated_at не уменьшается."""
    user, headers = await user_with_headers(db_session, client)
    first = await put_profile(client, headers, {"country": "Германия"})

    second = await put_profile(client, headers, {"country": "Франция"})
    await put_profile(client, headers, {"country": "Италия"})

    assert await profile_rows(db_session, user.id) == 1
    assert await profile_rows(db_session) == 1
    assert second["user_id"] == first["user_id"] == user.id
    assert parse_ts(second["updated_at"]) >= parse_ts(first["updated_at"])
    row = await db_session.get(GuestProfile, user.id)
    assert row is not None
    assert row.country == "Италия"


async def test_put_changes_only_own_profile(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 6 plan (нарушение): PUT другого пользователя меняет только его профиль."""
    user, headers = await user_with_headers(db_session, client)
    other, other_headers = await user_with_headers(db_session, client)
    await put_profile(client, headers, {"country": "Германия", "interests": ["горы"]})

    await put_profile(client, other_headers, {"country": "Франция"})

    mine = await client.get("/guests/me", headers=headers)
    assert mine.status_code == 200, mine.text
    assert mine.json()["user_id"] == user.id
    assert mine.json()["country"] == "Германия"
    assert mine.json()["interests"] == ["горы"]
    theirs = await client.get("/guests/me", headers=other_headers)
    assert theirs.json()["user_id"] == other.id
    assert theirs.json()["country"] == "Франция"
    assert await profile_rows(db_session) == 2


async def test_put_user_id_in_body_cannot_target_other_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 6 plan (нарушение): user_id в теле игнорируется — меняется только свой профиль,
    чужой не трогается."""
    user, headers = await user_with_headers(db_session, client)
    other, other_headers = await user_with_headers(db_session, client)
    await put_profile(client, other_headers, {"country": "Франция"})

    response = await client.put(
        "/guests/me", headers=headers, json={"user_id": other.id, "country": "Германия"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["user_id"] == user.id
    assert await profile_rows(db_session, user.id) == 1
    theirs = await client.get("/guests/me", headers=other_headers)
    assert theirs.json()["user_id"] == other.id
    assert theirs.json()["country"] == "Франция"


# --- нормализация ---


async def test_put_trims_country_and_interests(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 6 plan: пробелы по краям в country и элементах interests обрезаются."""
    user, headers = await user_with_headers(db_session, client)

    data = await put_profile(
        client, headers, {"country": "  Германия  ", "interests": ["  горы ", "кухня  "]}
    )

    assert data["country"] == "Германия"
    assert data["interests"] == ["горы", "кухня"]
    row = await db_session.get(GuestProfile, user.id)
    assert row is not None
    assert row.country == "Германия"
    assert list(row.interests) == ["горы", "кухня"]


# --- валидация: успешные границы ---


async def test_put_limits_at_boundary_accepted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 6 plan (граница): country 100 символов, 20 интересов по 50 символов — допустимы."""
    _, headers = await user_with_headers(db_session, client)
    country = "к" * 100
    interests = [f"{i:02d}" + "и" * 48 for i in range(20)]

    data = await put_profile(client, headers, {"country": country, "interests": interests})

    assert data["country"] == country
    assert data["interests"] == interests


@pytest.mark.parametrize("ext", ["jpg", "png", "webp"])
async def test_put_avatar_photo_url_accepted(
    client: AsyncClient, db_session: AsyncSession, ext: str
) -> None:
    """Step 6 plan: avatar_url — ссылка вида /files/<32 hex>.(jpg|png|webp)."""
    _, headers = await user_with_headers(db_session, client)
    url = avatar(ext)

    data = await put_profile(client, headers, {"avatar_url": url})

    assert data["avatar_url"] == url


async def test_put_avatar_from_real_upload_accepted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 6 plan: url, полученный от POST /uploads для фото, принимается как avatar_url."""
    _, headers = await user_with_headers(db_session, client)
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x01" * 64
    uploaded = await client.post(
        "/uploads", headers=headers, files={"file": ("a.jpg", jpeg, "image/jpeg")}
    )
    assert uploaded.status_code == 201, uploaded.text
    url = uploaded.json()["url"]

    data = await put_profile(client, headers, {"avatar_url": url})

    assert data["avatar_url"] == url


async def test_put_birth_date_today_accepted(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 6 plan (граница): birth_date = сегодня по Бишкеку допустима, возраст 0."""
    _, headers = await user_with_headers(db_session, client)
    today = bishkek_today()

    data = await put_profile(client, headers, {"birth_date": today.isoformat()})

    assert data["birth_date"] == today.isoformat()
    assert data["age"] == 0


# --- валидация: ошибки ---


async def test_put_birth_date_tomorrow_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 6 plan (нарушение): birth_date завтра по Бишкеку → 422, профиль не создаётся."""
    user, headers = await user_with_headers(db_session, client)
    tomorrow = bishkek_today() + timedelta(days=1)

    response = await client.put(
        "/guests/me", headers=headers, json={"birth_date": tomorrow.isoformat()}
    )

    assert_error(response, 422, "validation_error")
    assert await profile_rows(db_session, user.id) == 0


@pytest.mark.parametrize(
    "body",
    [
        {"country": ""},
        {"country": "   "},
        {"country": "к" * 101},
        {"interests": [f"и{i}" for i in range(21)]},
        {"interests": ["горы", ""]},
        {"interests": ["   "]},
        {"interests": ["и" * 51]},
        {"interests": "горы"},
        {"birth_date": "05.10.2000"},
        {"birth_date": "2000-02-30"},
        {"birth_date": "2999-01-01"},
    ],
    ids=[
        "country-empty",
        "country-blank",
        "country-101",
        "interests-21",
        "interest-empty",
        "interest-blank",
        "interest-51",
        "interests-not-list",
        "birth-bad-format",
        "birth-bad-day",
        "birth-far-future",
    ],
)
async def test_put_invalid_fields_rejected(
    client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    """Step 6 plan (нарушение): недопустимые country / interests / birth_date → 422."""
    user, headers = await user_with_headers(db_session, client)

    response = await client.put("/guests/me", headers=headers, json=body)

    assert_error(response, 422, "validation_error")
    assert await profile_rows(db_session, user.id) == 0


HEX = "0123456789abcdef" * 2


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/x.jpg",
        "/files/abc.jpg",
        f"/files/{HEX}.mp4",
        f"/files/{HEX.upper()}.jpg",
        f"/files/{HEX}.jpeg",
        f"http://test/files/{HEX}.jpg",
        f"/files/{HEX}.jpg?x=1",
        f"/files/{HEX}.jpg\n",
        f"/files/../{HEX}.jpg",
        f"/files/{HEX}0.jpg",
        "",
    ],
    ids=[
        "external",
        "short-name",
        "video",
        "uppercase-hex",
        "jpeg-ext",
        "absolute-url",
        "query",
        "trailing-newline",
        "traversal",
        "33-hex",
        "empty",
    ],
)
async def test_put_invalid_avatar_url_rejected(
    client: AsyncClient, db_session: AsyncSession, url: str
) -> None:
    """Step 6 plan (нарушение): avatar_url не ссылка на наш файл-фото → 422."""
    user, headers = await user_with_headers(db_session, client)

    response = await client.put("/guests/me", headers=headers, json={"avatar_url": url})

    assert_error(response, 422, "validation_error")
    assert await profile_rows(db_session, user.id) == 0


async def test_invalid_put_does_not_change_existing_profile(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 6 plan (нарушение): отклонённый PUT не трогает уже сохранённый профиль."""
    _, headers = await user_with_headers(db_session, client)
    saved = await put_profile(client, headers, {"country": "Германия", "interests": ["горы"]})

    response = await client.put(
        "/guests/me", headers=headers, json={"country": "Франция", "interests": [""]}
    )

    assert_error(response, 422, "validation_error")
    got = await client.get("/guests/me", headers=headers)
    assert got.json() == saved


# --- возраст через API ---


async def test_age_birthday_today_already_counted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 6 plan: день рождения сегодня (по Бишкеку) → возраст уже увеличился."""
    _, headers = await user_with_headers(db_session, client)
    birth = years_before(bishkek_today(), 30)

    data = await put_profile(client, headers, {"birth_date": birth.isoformat()})

    assert data["age"] == 30


async def test_age_birthday_tomorrow_not_yet_counted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 6 plan: день рождения завтра (по Бишкеку) → возраст ещё не увеличился."""
    _, headers = await user_with_headers(db_session, client)
    birth = years_before(bishkek_today() + timedelta(days=1), 30)

    data = await put_profile(client, headers, {"birth_date": birth.isoformat()})

    assert data["age"] == 29


# --- age_on: граничные случаи ---


@pytest.mark.parametrize(
    ("birth", "today", "expected"),
    [
        (date(2000, 10, 5), date(2000, 10, 5), 0),
        (date(1990, 10, 5), date(2020, 10, 5), 30),
        (date(1990, 10, 6), date(2020, 10, 5), 29),
        (date(1990, 10, 4), date(2020, 10, 5), 30),
        (date(1990, 12, 31), date(2021, 1, 1), 30),
        (date(1991, 1, 1), date(2020, 12, 31), 29),
        (date(2000, 2, 29), date(2025, 2, 28), 24),
        (date(2000, 2, 29), date(2025, 3, 1), 25),
        (date(2000, 2, 29), date(2024, 2, 28), 23),
        (date(2000, 2, 29), date(2024, 2, 29), 24),
        (date(2000, 3, 1), date(2024, 2, 29), 23),
    ],
    ids=[
        "born-today",
        "birthday-today",
        "birthday-tomorrow",
        "birthday-yesterday",
        "new-year-after",
        "new-year-before",
        "feb29-non-leap-feb28",
        "feb29-non-leap-mar1",
        "feb29-leap-feb28",
        "feb29-leap-feb29",
        "mar1-on-leap-feb29",
    ],
)
def test_age_on(birth: date, today: date, expected: int) -> None:
    """Step 6 plan: age_on — полных лет на дату today (граница дня рождения, 29 февраля)."""
    assert age_on(birth, today) == expected


# --- GuestPublicOut ---


async def test_public_view_hides_birth_date(client: AsyncClient, db_session: AsyncSession) -> None:
    """Step 6 plan: GuestPublicOut.from_profile — ровно user_id, country, age, avatar_url,
    interests; birth_date хозяину не отдаётся, age посчитан."""
    user, headers = await user_with_headers(db_session, client)
    birth = years_before(bishkek_today(), 25) - timedelta(days=3)
    url = avatar("webp")
    await put_profile(
        client,
        headers,
        {
            "country": "Германия",
            "birth_date": birth.isoformat(),
            "avatar_url": url,
            "interests": ["горы", "кони"],
        },
    )
    profile = await db_session.get(GuestProfile, user.id)
    assert profile is not None

    dumped = GuestPublicOut.from_profile(profile).model_dump()

    assert set(dumped) == PUBLIC_FIELDS
    assert "birth_date" not in dumped
    assert dumped["user_id"] == user.id
    assert dumped["country"] == "Германия"
    assert dumped["age"] == 25
    assert dumped["avatar_url"] == url
    assert dumped["interests"] == ["горы", "кони"]
    assert birth.isoformat() not in GuestPublicOut.from_profile(profile).model_dump_json()


async def test_public_view_without_birth_date(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Step 6 plan (граница): нет birth_date → в публичном виде age null."""
    user, headers = await user_with_headers(db_session, client)
    await put_profile(client, headers, {})
    profile = await db_session.get(GuestProfile, user.id)
    assert profile is not None

    dumped = GuestPublicOut.from_profile(profile).model_dump()

    assert set(dumped) == PUBLIC_FIELDS
    assert dumped["age"] is None
    assert dumped["country"] is None
    assert dumped["avatar_url"] is None
    assert dumped["interests"] == []
