"""Профиль хозяина: POST/GET /hosts/me, публичный GET /hosts/{user_id}.

R-29 — профиль создаётся одним запросом, черновиков нет; решение #9 — публикуется сразу;
R-07 — видео MP4 не длиннее 120 с, длительность сервер берёт из файла (бокс mvhd);
R-02 — хозяин = user с host_profiles, роль не меняется; R-03 — публично виден только при
kyc_status = 'approved' и is_published; R-06 — адрес и координаты публично не отдаются.
"""

import asyncio
import struct
import time
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import KycStatus, User
from tests.admin.helpers import assert_error
from tests.factories import auth, create_user, login

OWNER_FIELDS = {
    "user_id",
    "region_id",
    "settlement",
    "address",
    "latitude",
    "longitude",
    "about",
    "avatar_url",
    "photos",
    "video_url",
    "video_duration_sec",
    "capacity",
    "price_per_person_night",
    "is_guide",
    "is_published",
    "rating_avg",
    "reviews_count",
    "created_at",
    "updated_at",
}
PUBLIC_FIELDS = {
    "user_id",
    "region_id",
    "settlement",
    "about",
    "avatar_url",
    "photos",
    "video_url",
    "video_duration_sec",
    "capacity",
    "price_per_person_night",
    "is_guide",
    "rating_avg",
    "reviews_count",
}
REQUIRED_FIELDS = ("region_id", "settlement", "address", "capacity", "price_per_person_night")

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x01" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x02" * 64


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def mp4(duration: int, timescale: int = 1000, *, v1: bool = False, moov_last: bool = True) -> bytes:
    """MP4 с боксом moov/mvhd заданной длительности (duration / timescale секунд)."""
    if v1:
        mvhd = bytes([1, 0, 0, 0]) + struct.pack(">QQIQ", 0, 0, timescale, duration)
    else:
        mvhd = bytes([0, 0, 0, 0]) + struct.pack(">IIII", 0, 0, timescale, duration)
    moov = box(b"moov", box(b"mvhd", mvhd + b"\0" * 80))
    ftyp = box(b"ftyp", b"isom\0\0\x02\0isomiso2")
    mdat = box(b"mdat", b"\0" * 100)
    return ftyp + (mdat + moov if moov_last else moov + mdat)


def mp4_without_moov() -> bytes:
    return box(b"ftyp", b"isom\0\0\x02\0isomiso2") + box(b"mdat", b"\0" * 100)


def fake_photo(ext: str = "jpg") -> str:
    return f"/files/{uuid.uuid4().hex}.{ext}"


async def user_with_headers(
    db_session: AsyncSession, client: AsyncClient
) -> tuple[User, dict[str, str]]:
    user = await create_user(db_session)
    tokens = await login(client, user.email)
    return user, auth(tokens["access_token"])


async def first_region_id(client: AsyncClient) -> int:
    response = await client.get("/regions")
    assert response.status_code == 200, response.text
    regions = response.json()
    assert regions, "в сиде нет регионов"
    return int(regions[0]["id"])


async def upload(client: AsyncClient, headers: dict[str, str], content: bytes) -> str:
    response = await client.post(
        "/uploads",
        headers=headers,
        files={"file": ("upload.bin", content, "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    url: str = response.json()["url"]
    return url


def base_body(region_id: int, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "region_id": region_id,
        "settlement": "Кочкор",
        "address": "ул. Секретная, 7",
        "capacity": 4,
        "price_per_person_night": "1500.00",
    }
    body.update(overrides)
    return body


async def create_host(
    client: AsyncClient, headers: dict[str, str], body: dict[str, Any]
) -> dict[str, Any]:
    response = await client.post("/hosts/me", headers=headers, json=body)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


async def host_rows(db_session: AsyncSession, user_id: int | None = None) -> int:
    sql = "SELECT count(*) FROM host_profiles"
    params: dict[str, Any] = {}
    if user_id is not None:
        sql += " WHERE user_id = :uid"
        params["uid"] = user_id
    count: int = await db_session.scalar(text(sql), params) or 0
    return count


async def set_kyc(db_session: AsyncSession, user_id: int, status: KycStatus) -> None:
    await db_session.execute(update(User).where(User.id == user_id).values(kyc_status=status))
    await db_session.commit()


# --- без токена ---


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_hosts_me_requires_auth(client: AsyncClient, method: str) -> None:
    """/hosts/me без токена → 401 unauthorized."""
    response = await client.request(method, "/hosts/me", json={})
    assert_error(response, 401, "unauthorized")


# --- POST /hosts/me: успешное создание ---


async def test_create_minimal_profile(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-29, решение #9: полная анкета → 201, профиль сразу опубликован, рейтинг нулевой."""
    user, headers = await user_with_headers(db_session, client)
    region_id = await first_region_id(client)

    data = await create_host(client, headers, base_body(region_id))

    assert set(data) == OWNER_FIELDS
    assert data["user_id"] == user.id
    assert data["region_id"] == region_id
    assert data["settlement"] == "Кочкор"
    assert data["address"] == "ул. Секретная, 7"
    assert data["capacity"] == 4
    assert data["price_per_person_night"] == "1500.00"
    assert data["is_published"] is True
    assert data["is_guide"] is False
    assert data["rating_avg"] == "0.00"
    assert data["reviews_count"] == 0
    assert data["latitude"] is None
    assert data["longitude"] is None
    assert data["about"] is None
    assert data["avatar_url"] is None
    assert data["photos"] == []
    assert data["video_url"] is None
    assert data["video_duration_sec"] is None
    assert await host_rows(db_session, user.id) == 1


async def test_create_full_profile(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-29, R-07: все необязательные поля сохраняются, порядок фото сохраняется."""
    _, headers = await user_with_headers(db_session, client)
    region_id = await first_region_id(client)
    avatar = await upload(client, headers, JPEG)
    photos = [await upload(client, headers, PNG), await upload(client, headers, JPEG)]
    video = await upload(client, headers, mp4(60_000))

    data = await create_host(
        client,
        headers,
        base_body(
            region_id,
            about="Юрта у озера",
            avatar_url=avatar,
            photos=photos,
            video_url=video,
            latitude="42.123456",
            longitude="-75.654321",
        ),
    )

    assert data["about"] == "Юрта у озера"
    assert data["avatar_url"] == avatar
    assert data["photos"] == photos
    assert data["video_url"] == video
    assert data["video_duration_sec"] == 60
    assert data["latitude"] == "42.123456"
    assert data["longitude"] == "-75.654321"


async def test_create_trims_settlement_and_address(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-29: населённый пункт и адрес обрезаются по краям."""
    _, headers = await user_with_headers(db_session, client)
    region_id = await first_region_id(client)

    data = await create_host(
        client, headers, base_body(region_id, settlement="  Кочкор ", address=" ул. Ленина, 1  ")
    )

    assert data["settlement"] == "Кочкор"
    assert data["address"] == "ул. Ленина, 1"


async def test_create_ignores_is_guide_and_server_fields(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Решение #9: is_guide, рейтинг, публикация, user_id из тела не принимаются."""
    user, headers = await user_with_headers(db_session, client)
    other = await create_user(db_session)
    region_id = await first_region_id(client)

    data = await create_host(
        client,
        headers,
        base_body(
            region_id,
            is_guide=True,
            is_published=False,
            rating_avg="5.00",
            reviews_count=10,
            user_id=other.id,
        ),
    )

    assert data["is_guide"] is False
    assert data["is_published"] is True
    assert data["rating_avg"] == "0.00"
    assert data["reviews_count"] == 0
    assert data["user_id"] == user.id
    assert await host_rows(db_session, other.id) == 0


@pytest.mark.parametrize(
    ("lat", "lon"),
    [("90", "180"), ("-90", "-180"), ("0", "0"), (42.5, 74.6)],
    ids=["max", "min", "zero", "numbers"],
)
async def test_create_coordinates_boundaries_accepted(
    client: AsyncClient, db_session: AsyncSession, lat: Any, lon: Any
) -> None:
    """R-29: координаты на границах диапазонов принимаются."""
    _, headers = await user_with_headers(db_session, client)
    region_id = await first_region_id(client)

    data = await create_host(client, headers, base_body(region_id, latitude=lat, longitude=lon))

    assert float(data["latitude"]) == float(lat)
    assert float(data["longitude"]) == float(lon)


async def test_create_limits_at_boundary_accepted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-29: about ровно 5000 символов и ровно 20 фото принимаются."""
    _, headers = await user_with_headers(db_session, client)
    region_id = await first_region_id(client)
    photos = [fake_photo("webp") for _ in range(20)]

    data = await create_host(
        client,
        headers,
        base_body(region_id, about="а" * 5000, photos=photos, price_per_person_night="0.01"),
    )

    assert len(data["about"]) == 5000
    assert data["photos"] == photos
    assert data["price_per_person_night"] == "0.01"


# --- POST /hosts/me: валидация ---


@pytest.mark.parametrize("missing", REQUIRED_FIELDS)
async def test_create_missing_required_field_rejected(
    client: AsyncClient, db_session: AsyncSession, missing: str
) -> None:
    """R-29: без любого обязательного поля → 422, черновик не создаётся."""
    _, headers = await user_with_headers(db_session, client)
    body = base_body(await first_region_id(client))
    del body[missing]

    response = await client.post("/hosts/me", headers=headers, json=body)

    assert_error(response, 422, "validation_error")
    assert await host_rows(db_session) == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"settlement": ""},
        {"settlement": "   "},
        {"address": ""},
        {"address": "  \t "},
        {"settlement": None},
        {"address": None},
        {"capacity": 0},
        {"capacity": -1},
        {"price_per_person_night": "0"},
        {"price_per_person_night": "-100.00"},
        {"price_per_person_night": "10.123"},
        {"about": "а" * 5001},
        {"avatar_url": "https://evil.example/a.jpg"},
        {"avatar_url": "/files/abc.jpg"},
        {"avatar_url": f"/files/{'a' * 32}.mp4"},
        {"avatar_url": f"/files/{'A' * 32}.jpg"},
        {"avatar_url": f"/files/{'a' * 32}.jpg?x=1"},
        {"photos": ["https://evil.example/a.jpg"]},
        {"photos": [f"/files/{'a' * 32}.mp4"]},
        {"photos": [f"/files/{i:032x}.jpg" for i in range(21)]},
        {"video_url": f"/files/{'a' * 32}.jpg"},
        {"video_url": "https://evil.example/v.mp4"},
        {"video_url": "/files/../secret.mp4"},
        {"latitude": "42.5"},
        {"longitude": "74.6"},
        {"latitude": "90.000001", "longitude": "0"},
        {"latitude": "-90.5", "longitude": "0"},
        {"latitude": "0", "longitude": "180.000001"},
        {"latitude": "0", "longitude": "-181"},
        {"latitude": "42.1234567", "longitude": "74.6"},
        {"latitude": "42.5", "longitude": "74.1234567"},
    ],
    ids=[
        "settlement-empty",
        "settlement-spaces",
        "address-empty",
        "address-spaces",
        "settlement-null",
        "address-null",
        "capacity-zero",
        "capacity-negative",
        "price-zero",
        "price-negative",
        "price-3-decimals",
        "about-too-long",
        "avatar-external",
        "avatar-short-hex",
        "avatar-video",
        "avatar-uppercase-hex",
        "avatar-query",
        "photos-external",
        "photos-video",
        "photos-21",
        "video-photo",
        "video-external",
        "video-traversal",
        "latitude-only",
        "longitude-only",
        "latitude-above-90",
        "latitude-below-90",
        "longitude-above-180",
        "longitude-below-180",
        "latitude-7-decimals",
        "longitude-7-decimals",
    ],
)
async def test_create_invalid_fields_rejected(
    client: AsyncClient, db_session: AsyncSession, overrides: dict[str, Any]
) -> None:
    """R-29, R-07: некорректные поля → 422 validation_error, профиль не создаётся."""
    _, headers = await user_with_headers(db_session, client)
    body = base_body(await first_region_id(client), **overrides)

    response = await client.post("/hosts/me", headers=headers, json=body)

    assert_error(response, 422, "validation_error")
    assert await host_rows(db_session) == 0


async def test_create_unknown_region_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-29: несуществующий регион → 404 not_found, профиль не создаётся."""
    _, headers = await user_with_headers(db_session, client)

    response = await client.post("/hosts/me", headers=headers, json=base_body(30000))

    assert_error(response, 404, "not_found")
    assert await host_rows(db_session) == 0


async def test_create_twice_conflict(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-29: повторное создание → 409 conflict, первый профиль не меняется."""
    user, headers = await user_with_headers(db_session, client)
    region_id = await first_region_id(client)
    await create_host(client, headers, base_body(region_id))

    response = await client.post(
        "/hosts/me", headers=headers, json=base_body(region_id, settlement="Другое")
    )

    assert_error(response, 409, "conflict")
    assert await host_rows(db_session, user.id) == 1
    me = await client.get("/hosts/me", headers=headers)
    assert me.json()["settlement"] == "Кочкор"


# --- POST /hosts/me: видео (R-07) ---


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (mp4(90_500), 91),
        (mp4(120_000), 120),
        (mp4(1), 1),
        (mp4(45, timescale=1), 45),
        (mp4(90_500, v1=True), 91),
        (mp4(120_000, moov_last=False), 120),
        (mp4(30_000, v1=True, moov_last=False), 30),
    ],
    ids=["90.5s-ceil", "exact-120s", "1ms-ceil", "timescale-1", "v1", "moov-first", "v1-first"],
)
async def test_create_video_duration_from_file(
    client: AsyncClient, db_session: AsyncSession, content: bytes, expected: int
) -> None:
    """R-07: длительность берётся из mvhd и округляется вверх."""
    _, headers = await user_with_headers(db_session, client)
    region_id = await first_region_id(client)
    video = await upload(client, headers, content)

    data = await create_host(client, headers, base_body(region_id, video_url=video))

    assert data["video_url"] == video
    assert data["video_duration_sec"] == expected


async def test_create_video_duration_from_client_ignored(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-07: video_duration_sec из тела игнорируется, сервер не верит клиенту."""
    _, headers = await user_with_headers(db_session, client)
    region_id = await first_region_id(client)
    video = await upload(client, headers, mp4(10_000))

    data = await create_host(
        client, headers, base_body(region_id, video_url=video, video_duration_sec=100)
    )

    assert data["video_duration_sec"] == 10


async def test_create_duration_without_video_ignored(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-07: video_duration_sec без видео не сохраняется."""
    _, headers = await user_with_headers(db_session, client)
    region_id = await first_region_id(client)

    data = await create_host(client, headers, base_body(region_id, video_duration_sec=50))

    assert data["video_url"] is None
    assert data["video_duration_sec"] is None


@pytest.mark.parametrize(
    "content",
    [mp4(120_001), mp4(121, timescale=1), mp4(600_000, v1=True, moov_last=False)],
    ids=["120.001s", "121s", "v1-10min"],
)
async def test_create_video_too_long_rejected(
    client: AsyncClient, db_session: AsyncSession, content: bytes
) -> None:
    """R-07: видео длиннее 120 секунд → 400 invalid_file, профиль не создаётся."""
    _, headers = await user_with_headers(db_session, client)
    region_id = await first_region_id(client)
    video = await upload(client, headers, content)

    response = await client.post(
        "/hosts/me", headers=headers, json=base_body(region_id, video_url=video)
    )

    assert_error(response, 400, "invalid_file")
    assert await host_rows(db_session) == 0


async def test_create_video_missing_on_disk_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-07: ссылка на несуществующий файл → 400 invalid_file."""
    _, headers = await user_with_headers(db_session, client)
    region_id = await first_region_id(client)

    response = await client.post(
        "/hosts/me",
        headers=headers,
        json=base_body(region_id, video_url=f"/files/{uuid.uuid4().hex}.mp4"),
    )

    assert_error(response, 400, "invalid_file")
    assert await host_rows(db_session) == 0


async def test_create_video_without_mvhd_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-07: mp4 без moov/mvhd — длительность не определить → 400 invalid_file."""
    _, headers = await user_with_headers(db_session, client)
    region_id = await first_region_id(client)
    video = await upload(client, headers, mp4_without_moov())

    response = await client.post(
        "/hosts/me", headers=headers, json=base_body(region_id, video_url=video)
    )

    assert_error(response, 400, "invalid_file")
    assert await host_rows(db_session) == 0


# --- R-02: роль не меняется ---


async def test_host_keeps_user_role(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-02: хозяин — это user с профилем; роль остаётся user."""
    _, headers = await user_with_headers(db_session, client)
    await create_host(client, headers, base_body(await first_region_id(client)))

    me = await client.get("/hosts/me", headers=headers)
    assert me.status_code == 200, me.text
    user_me = await client.get("/users/me", headers=headers)
    assert user_me.status_code == 200, user_me.text
    assert user_me.json()["role"] == "user"


# --- GET /hosts/me ---


async def test_get_me_without_profile_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-02: без профиля пользователь не хозяин → 404 not_found."""
    _, headers = await user_with_headers(db_session, client)

    response = await client.get("/hosts/me", headers=headers)

    assert_error(response, 404, "not_found")


async def test_get_me_returns_own_profile_with_address(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-06: владелец видит свой адрес и координаты."""
    user, headers = await user_with_headers(db_session, client)
    created = await create_host(
        client,
        headers,
        base_body(await first_region_id(client), latitude="42.1", longitude="74.2"),
    )

    response = await client.get("/hosts/me", headers=headers)

    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data) == OWNER_FIELDS
    assert data == created
    assert data["user_id"] == user.id
    assert data["address"] == "ул. Секретная, 7"
    assert float(data["latitude"]) == 42.1


async def test_get_me_does_not_return_other_users_profile(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-02: чужой профиль хозяина не делает меня хозяином."""
    _, host_headers = await user_with_headers(db_session, client)
    await create_host(client, host_headers, base_body(await first_region_id(client)))
    _, headers = await user_with_headers(db_session, client)

    response = await client.get("/hosts/me", headers=headers)

    assert_error(response, 404, "not_found")


# --- GET /hosts/{user_id} (публично) ---


async def test_public_profile_approved_kyc(client: AsyncClient, db_session: AsyncSession) -> None:
    """R-03, R-06: опубликованный хозяин с approved KYC виден без токена, без адреса."""
    user, headers = await user_with_headers(db_session, client)
    created = await create_host(
        client,
        headers,
        base_body(
            await first_region_id(client),
            address="ул. Уникальныйадрес, 99",
            latitude="41.987654",
            longitude="75.123987",
        ),
    )
    await set_kyc(db_session, user.id, KycStatus.APPROVED)

    response = await client.get(f"/hosts/{user.id}")

    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data) == PUBLIC_FIELDS
    for key in PUBLIC_FIELDS:
        assert data[key] == created[key], key
    raw = response.text
    assert "Уникальныйадрес" not in raw
    assert "41.98" not in raw
    assert "75.12" not in raw


@pytest.mark.parametrize(
    "status",
    [s for s in KycStatus if s != KycStatus.APPROVED],
)
async def test_public_profile_without_approved_kyc_hidden(
    client: AsyncClient, db_session: AsyncSession, status: KycStatus
) -> None:
    """R-03: без kyc_status = approved хозяин публично не виден → 404 not_found."""
    user, headers = await user_with_headers(db_session, client)
    await create_host(client, headers, base_body(await first_region_id(client)))
    await set_kyc(db_session, user.id, status)

    response = await client.get(f"/hosts/{user.id}")

    assert_error(response, 404, "not_found")


async def test_public_profile_unpublished_hidden(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-03: снятый с публикации профиль не виден даже при approved KYC."""
    user, headers = await user_with_headers(db_session, client)
    await create_host(client, headers, base_body(await first_region_id(client)))
    await set_kyc(db_session, user.id, KycStatus.APPROVED)
    await db_session.execute(
        text("UPDATE host_profiles SET is_published = false WHERE user_id = :uid"),
        {"uid": user.id},
    )
    await db_session.commit()

    response = await client.get(f"/hosts/{user.id}")

    assert_error(response, 404, "not_found")


async def test_public_profile_missing_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-02, R-03: пользователь без профиля и несуществующий id → 404 not_found."""
    user = await create_user(db_session)
    await set_kyc(db_session, user.id, KycStatus.APPROVED)

    assert_error(await client.get(f"/hosts/{user.id}"), 404, "not_found")
    assert_error(await client.get("/hosts/999999999"), 404, "not_found")


@pytest.mark.parametrize(
    "user_id",
    ["0", "-1", str(2**63), "abc"],
    ids=["zero", "negative", "above-bigint", "not-int"],
)
async def test_public_profile_invalid_id_rejected(client: AsyncClient, user_id: str) -> None:
    """R-03: id вне BIGINT или не положительный → 422 validation_error."""
    response = await client.get(f"/hosts/{user_id}")

    assert_error(response, 422, "validation_error")


async def test_parallel_create_makes_one_profile(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-29: два одновременных создания — один профиль, второй запрос 409."""
    user, headers = await user_with_headers(db_session, client)
    body = base_body(await first_region_id(client))

    responses = await asyncio.gather(
        client.post("/hosts/me", headers=headers, json=body),
        client.post("/hosts/me", headers=headers, json=body),
    )

    assert sorted(r.status_code for r in responses) == [201, 409]
    assert await host_rows(db_session, user.id) == 1


async def test_video_with_too_many_boxes_rejected_quickly(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-07: файл из сотен тысяч пустых боксов не разбирается целиком — 400, без зависания."""
    user, headers = await user_with_headers(db_session, client)
    junk = box(b"ftyp", b"isom\0\0\x02\0isomiso2") + box(b"free", b"") * 200_000 + mp4(10_000)
    video_url = await upload(client, headers, junk)

    started = time.monotonic()
    response = await client.post(
        "/hosts/me",
        headers=headers,
        json=base_body(await first_region_id(client), video_url=video_url),
    )

    assert_error(response, 400, "invalid_file")
    assert time.monotonic() - started < 2
    assert await host_rows(db_session, user.id) == 0
