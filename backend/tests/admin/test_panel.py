"""Админ-панель SQLAdmin на /admin-panel.

R-41 — вход только admin со status = active, роль и статус перепроверяются на каждом запросе;
regions без удаления, settings только правка значения с проверкой формата, audit_logs только
чтение; статусы с побочными эффектами меняются кнопками через сервисы; хеш пароля и PIN не
показываются; каждое создание / правка / удаление через панель пишется в audit_logs.
Затронуты: R-28 (аудит блокировки, KYC, настроек), R-23 (PIN при одобрении KYC),
R-39 (уведомление о решении KYC, о выплате), R-35 (выплата владельцу машины при completed).
"""

import json
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Response
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.audit import AuditLog
from app.main import create_app
from app.modules.admin.models import Region, Setting, Tag
from app.modules.admin.panel import VIEWS
from app.modules.bookings.models import Booking, BookingStatus
from app.modules.hosts.models import HostProfile
from app.modules.kyc.models import KycVerification
from app.modules.notifications.models import Notification
from app.modules.payments.models import PayoutStatus
from app.modules.users.models import KycStatus, User, UserRole, UserStatus
from tests.bookings.test_checkin import give_pin, payouts_of
from tests.bookings.test_close import end_stay_period
from tests.bookings.test_stay_bookings import guest_with_headers
from tests.bookings.test_vehicle_bookings import make_vehicle, rent, rent_body
from tests.factories import DEFAULT_PASSWORD, create_user, login
from tests.kyc.test_kyc import submit, user_with_headers
from tests.payments.test_pay import notifications_of, pay

BASE = "/admin-panel"
LOGIN_URL = f"{BASE}/login"
IDENTITIES = [view.identity for view in VIEWS]
NEW_PASSWORD = "PanelPass123!"


@pytest.fixture
async def panel(
    engine: AsyncEngine, redis: Redis, client: AsyncClient
) -> AsyncIterator[AsyncClient]:
    # dependency_overrides на SQLAdmin не действуют — engine и redis передаются в create_app.
    # client — только ради порядка: SQLAdmin пишет session_maker в атрибуты классов VIEWS,
    # поэтому побеждает последний create_app(). Фикстура client вызывает create_app() с
    # глобальным engine; панель должна монтироваться после неё, иначе работает не на том engine.
    app = create_app(panel_engine=engine, panel_redis=redis)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# --- хелперы -----------------------------------------------------------------


async def panel_login(panel: AsyncClient, email: str, password: str = DEFAULT_PASSWORD) -> Response:
    return await panel.post(LOGIN_URL, data={"username": email, "password": password})


async def logged_admin(panel: AsyncClient, db_session: AsyncSession) -> User:
    admin = await create_user(db_session, role=UserRole.ADMIN)
    response = await panel_login(panel, admin.email)
    assert response.status_code == 302, response.text
    return admin


def assert_redirect_to_login(response: Response) -> None:
    assert response.status_code == 302, response.text
    assert response.headers["location"].endswith(LOGIN_URL), response.headers["location"]


async def fresh(db_session: AsyncSession, model: Any, pk: Any) -> Any:
    """Строка из БД заново (панель коммитит в своей сессии)."""
    return await db_session.get(model, pk, populate_existing=True)


async def audit_rows(
    db_session: AsyncSession, action: str, entity_type: str | None = None
) -> list[AuditLog]:
    query = select(AuditLog).where(AuditLog.action == action)
    if entity_type is not None:
        query = query.where(AuditLog.entity_type == entity_type)
    rows = await db_session.scalars(query.order_by(AuditLog.id))
    return list(rows.all())


async def first_region(db_session: AsyncSession) -> Region:
    region = await db_session.scalar(select(Region).order_by(Region.id).limit(1))
    assert region is not None, "в сидах нет регионов"
    return region


async def follow(panel: AsyncClient, response: Response) -> Response:
    """Открывает страницу редиректа кнопки-действия: там flash-сообщение с итогом."""
    assert response.status_code == 302, response.text
    return await panel.get(response.headers["location"])


def user_form(email: str, **overrides: str) -> dict[str, str]:
    data = {
        "role": "user",
        "first_name": "Panel",
        "last_name": "Created",
        "email": email,
        "phone": "+996555123456",
        "terms_accepted_at": "2026-10-01 10:00:00",
        "new_password": NEW_PASSWORD,
    }
    data.update(overrides)
    return data


def user_edit_form(user: User, **overrides: str) -> dict[str, str]:
    data = {
        "role": user.role.value,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "phone": user.phone,
        "terms_accepted_at": user.terms_accepted_at.strftime("%Y-%m-%d %H:%M:%S"),
    }
    data.update(overrides)
    return data


def host_form(user_id: int, region_id: int, blocked_dates: list[str]) -> dict[str, Any]:
    return {
        "user_id": str(user_id),
        "region_id": str(region_id),
        "settlement": "Кочкор",
        "address": "ул. Панельная, 1",
        "capacity": "3",
        "price_per_person_night": "1500.00",
        "blocked_dates": blocked_dates,
        "rating_avg": "0",
        "reviews_count": "0",
    }


# --- 1. Доступ ---------------------------------------------------------------


async def test_admin_logs_in(panel: AsyncClient, db_session: AsyncSession) -> None:
    """R-41: active admin входит (email без учёта регистра) → 302 на панель, страницы доступны."""
    admin = await create_user(db_session, role=UserRole.ADMIN, email="Boss@Example.kg")

    response = await panel_login(panel, "  BOSS@example.KG ")

    assert response.status_code == 302, response.text
    assert response.headers["location"].rstrip("/").endswith(BASE)
    page = await panel.get(f"{BASE}/user/list")
    assert page.status_code == 200, page.text
    assert admin.email in page.text


@pytest.mark.parametrize("role", [UserRole.USER, UserRole.MODERATOR])
async def test_non_admin_cannot_log_in(
    panel: AsyncClient, db_session: AsyncSession, role: UserRole
) -> None:
    """R-41: user и moderator с верным паролем не входят → 400, страницы закрыты."""
    user = await create_user(db_session, role=role)

    assert (await panel_login(panel, user.email)).status_code == 400
    assert_redirect_to_login(await panel.get(f"{BASE}/user/list"))


async def test_blocked_admin_cannot_log_in(panel: AsyncClient, db_session: AsyncSession) -> None:
    """R-41: admin со status = blocked не входит → 400."""
    admin = await create_user(db_session, role=UserRole.ADMIN, status=UserStatus.BLOCKED)

    assert (await panel_login(panel, admin.email)).status_code == 400
    assert_redirect_to_login(await panel.get(f"{BASE}/user/list"))


async def test_wrong_password_or_unknown_email(
    panel: AsyncClient, db_session: AsyncSession
) -> None:
    """R-41: неверный пароль admin и неизвестный email → 400."""
    admin = await create_user(db_session, role=UserRole.ADMIN)

    assert (await panel_login(panel, admin.email, "WrongPass123!")).status_code == 400
    assert (await panel_login(panel, "nobody@example.kg")).status_code == 400
    assert_redirect_to_login(await panel.get(f"{BASE}/user/list"))


@pytest.mark.parametrize(
    "path", ["/", "/user/list", "/setting/edit/hold_minutes", "/user/action/block?pks=1"]
)
async def test_without_login_redirects(panel: AsyncClient, path: str) -> None:
    """R-41: без входа любая страница и кнопка-действие → 302 на /admin-panel/login."""
    assert_redirect_to_login(await panel.get(f"{BASE}{path}"))


async def test_logout(panel: AsyncClient, db_session: AsyncSession) -> None:
    """R-41: после выхода страницы снова закрыты."""
    await logged_admin(panel, db_session)
    assert (await panel.get(f"{BASE}/user/list")).status_code == 200

    await panel.get(f"{BASE}/logout")

    assert_redirect_to_login(await panel.get(f"{BASE}/user/list"))


@pytest.mark.parametrize(
    "change",
    [{"status": UserStatus.BLOCKED}, {"role": UserRole.MODERATOR}, {"role": UserRole.USER}],
)
async def test_rechecked_on_every_request(
    panel: AsyncClient, db_session: AsyncSession, change: dict[str, Any]
) -> None:
    """R-41: admin заблокирован или понижен после входа — следующий запрос → 302 на login,
    и после возврата прав старая сессия уже не действует (сброшена)."""
    admin = await logged_admin(panel, db_session)
    assert (await panel.get(f"{BASE}/user/list")).status_code == 200

    await db_session.execute(update(User).where(User.id == admin.id).values(**change))
    await db_session.commit()

    assert_redirect_to_login(await panel.get(f"{BASE}/user/list"))
    assert_redirect_to_login(await panel.get(f"{BASE}/user/action/block?pks={admin.id}"))

    await db_session.execute(
        update(User)
        .where(User.id == admin.id)
        .values(role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    )
    await db_session.commit()
    assert_redirect_to_login(await panel.get(f"{BASE}/user/list"))


# --- 2. Все представления открываются ----------------------------------------


def test_views_cover_expected_tables() -> None:
    """R-41: панель покрывает все таблицы схемы (полный CRUD всех таблиц)."""
    assert sorted(IDENTITIES) == sorted(
        [
            "user",
            "kyc-verification",
            "guest-profile",
            "host-profile",
            "activity",
            "vehicle",
            "booking",
            "booking-activity",
            "payment",
            "payout",
            "refund",
            "review",
            "conversation",
            "message",
            "support-ticket",
            "favorite",
            "notification",
            "region",
            "tag",
            "place",
            "setting",
            "audit-log",
        ]
    )


@pytest.mark.parametrize("identity", IDENTITIES)
async def test_list_opens(panel: AsyncClient, db_session: AsyncSession, identity: str) -> None:
    """R-41: список каждого представления открывается у admin → 200."""
    await logged_admin(panel, db_session)

    response = await panel.get(f"{BASE}/{identity}/list")

    assert response.status_code == 200, response.text


async def test_lists_and_details_with_data(
    panel: AsyncClient, client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-41 (граница): списки открываются и с данными — после брони машины с оплатой,
    выплатой, уведомлениями и аудитом; детали брони и выплаты тоже открываются."""
    booking_id, _ = await paid_vehicle_booking(db_session, client)
    await end_stay_period(db_session, booking_id)
    await logged_admin(panel, db_session)
    assert (await panel.get(f"{BASE}/booking/action/complete?pks={booking_id}")).status_code == 302
    [payout] = await payouts_of(db_session, booking_id)

    for identity in IDENTITIES:
        response = await panel.get(f"{BASE}/{identity}/list")
        assert response.status_code == 200, f"{identity}: {response.text}"
    for path in (f"booking/details/{booking_id}", f"payout/details/{payout.id}"):
        response = await panel.get(f"{BASE}/{path}")
        assert response.status_code == 200, f"{path}: {response.text}"


# --- 3. Ограничения R-41: region, audit-log, setting -------------------------


async def test_region_cannot_be_deleted(panel: AsyncClient, db_session: AsyncSession) -> None:
    """R-41: регион не удаляется → 403, строка на месте."""
    await logged_admin(panel, db_session)
    region = await first_region(db_session)

    response = await panel.delete(f"{BASE}/region/delete", params={"pks": str(region.id)})

    assert response.status_code == 403, response.text
    assert await fresh(db_session, Region, region.id) is not None


async def test_region_create_and_rename(panel: AsyncClient, db_session: AsyncSession) -> None:
    """R-41: регион создаётся и переименовывается через панель."""
    await logged_admin(panel, db_session)

    created = await panel.post(f"{BASE}/region/create", data={"name": "Панельный"})
    assert created.status_code == 302, created.text
    region = await db_session.scalar(select(Region).where(Region.name == "Панельный"))
    assert region is not None

    renamed = await panel.post(f"{BASE}/region/edit/{region.id}", data={"name": "Переименованный"})
    assert renamed.status_code == 302, renamed.text
    assert (await fresh(db_session, Region, region.id)).name == "Переименованный"


async def test_region_unique_name_enforced(panel: AsyncClient, db_session: AsyncSession) -> None:
    """R-41 (ограничения БД действуют): регион с уже существующим именем → 400, дубля нет."""
    await logged_admin(panel, db_session)
    region = await first_region(db_session)

    response = await panel.post(f"{BASE}/region/create", data={"name": region.name})

    assert response.status_code == 400, response.text
    assert "alert-danger" in response.text
    names = (await db_session.scalars(select(Region.name).where(Region.name == region.name))).all()
    assert len(names) == 1


async def test_audit_log_read_only(panel: AsyncClient, db_session: AsyncSession) -> None:
    """R-41, R-28: журнал только читается — create / edit → 403, delete → 403; details — 200."""
    admin = await logged_admin(panel, db_session)
    target = await create_user(db_session)
    assert (await panel.get(f"{BASE}/user/action/block?pks={target.id}")).status_code == 302
    [row] = await audit_rows(db_session, "user_blocked")

    assert (await panel.get(f"{BASE}/audit-log/details/{row.id}")).status_code == 200
    assert (await panel.get(f"{BASE}/audit-log/create")).status_code == 403
    assert (await panel.get(f"{BASE}/audit-log/edit/{row.id}")).status_code == 403
    edit = await panel.post(f"{BASE}/audit-log/edit/{row.id}", data={"action": "forged"})
    assert edit.status_code == 403, edit.text
    delete = await panel.delete(f"{BASE}/audit-log/delete", params={"pks": str(row.id)})
    assert delete.status_code == 403, delete.text

    kept = await fresh(db_session, AuditLog, row.id)
    assert kept is not None
    assert (kept.action, kept.actor_id) == ("user_blocked", admin.id)


async def test_setting_no_create_no_delete(panel: AsyncClient, db_session: AsyncSession) -> None:
    """R-41: настройку нельзя создать или удалить → 403, ключ на месте."""
    await logged_admin(panel, db_session)

    assert (await panel.get(f"{BASE}/setting/create")).status_code == 403
    create = await panel.post(f"{BASE}/setting/create", data={"key": "x", "value": "1"})
    assert create.status_code == 403, create.text
    delete = await panel.delete(f"{BASE}/setting/delete", params={"pks": "hold_minutes"})
    assert delete.status_code == 403, delete.text
    assert await fresh(db_session, Setting, "hold_minutes") is not None
    assert await fresh(db_session, Setting, "x") is None


async def test_setting_invalid_value_rejected(panel: AsyncClient, db_session: AsyncSession) -> None:
    """R-41, R-28: неверный формат значения → 400 с ошибкой, значение и аудит не меняются."""
    await logged_admin(panel, db_session)
    before = (await fresh(db_session, Setting, "hold_minutes")).value

    for bad in ("abc", "0", "-5"):
        response = await panel.post(f"{BASE}/setting/edit/hold_minutes", data={"value": bad})
        assert response.status_code == 400, f"{bad}: {response.text}"
        assert "alert-danger" in response.text

    assert (await fresh(db_session, Setting, "hold_minutes")).value == before
    assert await audit_rows(db_session, "setting_updated") == []


async def test_setting_update(panel: AsyncClient, db_session: AsyncSession) -> None:
    """R-41, R-28: верное значение → 302, value изменено, updated_by = admin,
    аудит setting_updated со старым и новым значением."""
    admin = await logged_admin(panel, db_session)
    old = (await fresh(db_session, Setting, "hold_minutes")).value
    new = "21" if old != "21" else "22"

    response = await panel.post(f"{BASE}/setting/edit/hold_minutes", data={"value": new})

    assert response.status_code == 302, response.text
    setting = await fresh(db_session, Setting, "hold_minutes")
    assert (setting.value, setting.updated_by) == (new, admin.id)
    [row] = await audit_rows(db_session, "setting_updated")
    assert row.actor_id == admin.id
    assert row.details == {"key": "hold_minutes", "old_value": old, "new_value": new}


async def test_setting_key_not_editable(panel: AsyncClient, db_session: AsyncSession) -> None:
    """R-41 (граница): в форме только value — переданный key игнорируется, ключ не меняется."""
    await logged_admin(panel, db_session)

    response = await panel.post(
        f"{BASE}/setting/edit/hold_minutes", data={"key": "hacked", "value": "30"}
    )

    assert response.status_code == 302, response.text
    assert (await fresh(db_session, Setting, "hold_minutes")).value == "30"
    assert await fresh(db_session, Setting, "hacked") is None


# --- 4. Пользователь: пароль и секреты ---------------------------------------


async def test_create_user_with_password(
    panel: AsyncClient, client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-41: пароль задаётся полем «новый пароль» — созданный пользователь входит через API."""
    await logged_admin(panel, db_session)

    response = await panel.post(f"{BASE}/user/create", data=user_form("created@example.kg"))

    assert response.status_code == 302, response.text
    user = await db_session.scalar(select(User).where(User.email == "created@example.kg"))
    assert user is not None
    assert user.password_hash != NEW_PASSWORD
    assert (user.status, user.kyc_status, user.checkin_pin) == (
        UserStatus.ACTIVE,
        KycStatus.NOT_STARTED,
        None,
    )
    tokens = await login(client, "created@example.kg", NEW_PASSWORD)
    assert tokens["access_token"]


async def test_create_user_without_password(panel: AsyncClient, db_session: AsyncSession) -> None:
    """R-41: создание без нового пароля → 400 «New password is required», пользователя нет."""
    await logged_admin(panel, db_session)
    data = user_form("nopass@example.kg")
    del data["new_password"]

    response = await panel.post(f"{BASE}/user/create", data=data)

    assert response.status_code == 400, response.text
    assert "alert-danger" in response.text
    assert "New password is required" in response.text
    assert await db_session.scalar(select(User).where(User.email == "nopass@example.kg")) is None


async def test_create_user_short_password(panel: AsyncClient, db_session: AsyncSession) -> None:
    """R-41 (граница): пароль короче 8 символов → 400; ровно 8 — принимается."""
    await logged_admin(panel, db_session)

    short = await panel.post(
        f"{BASE}/user/create", data=user_form("short@example.kg", new_password="Ab1!xyz")
    )
    assert short.status_code == 400, short.text
    assert await db_session.scalar(select(User).where(User.email == "short@example.kg")) is None

    exact = await panel.post(
        f"{BASE}/user/create", data=user_form("exact@example.kg", new_password="Ab1!xyzw")
    )
    assert exact.status_code == 302, exact.text


async def test_edit_user_password(
    panel: AsyncClient, client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-41: правка без new_password хеш не меняет; с new_password — входит новый пароль."""
    await logged_admin(panel, db_session)
    user = await create_user(db_session)
    old_hash = user.password_hash

    keep = await panel.post(
        f"{BASE}/user/edit/{user.id}", data=user_edit_form(user, first_name="Renamed")
    )
    assert keep.status_code == 302, keep.text
    row = await fresh(db_session, User, user.id)
    assert (row.first_name, row.password_hash) == ("Renamed", old_hash)

    change = await panel.post(
        f"{BASE}/user/edit/{user.id}", data=user_edit_form(user, new_password=NEW_PASSWORD)
    )
    assert change.status_code == 302, change.text
    assert (await fresh(db_session, User, user.id)).password_hash != old_hash
    assert (await login(client, user.email, NEW_PASSWORD))["access_token"]
    old_login = await client.post(
        "/auth/login", json={"email": user.email, "password": DEFAULT_PASSWORD}
    )
    assert old_login.status_code == 401, old_login.text


async def test_user_form_has_no_side_effect_fields(
    panel: AsyncClient, db_session: AsyncSession
) -> None:
    """R-41: status, kyc_status, password_hash, checkin_pin не входят в форму; переданные
    вручную — игнорируются."""
    await logged_admin(panel, db_session)
    user = await create_user(db_session)
    await give_pin(db_session, user.id, "918273")

    page = await panel.get(f"{BASE}/user/edit/{user.id}")
    assert page.status_code == 200, page.text
    for field in ("status", "kyc_status", "password_hash", "checkin_pin"):
        assert f'name="{field}"' not in page.text, field
    assert 'name="new_password"' in page.text

    response = await panel.post(
        f"{BASE}/user/edit/{user.id}",
        data=user_edit_form(
            user,
            status="blocked",
            kyc_status="approved",
            password_hash="x",
            checkin_pin="000000",
        ),
    )
    assert response.status_code == 302, response.text
    row = await fresh(db_session, User, user.id)
    assert (row.status, row.kyc_status, row.checkin_pin) == (
        UserStatus.ACTIVE,
        KycStatus.NOT_STARTED,
        "918273",
    )
    assert row.password_hash == user.password_hash


async def test_user_details_hide_secrets(panel: AsyncClient, db_session: AsyncSession) -> None:
    """R-41, R-23: в деталях и списке пользователя нет хеша пароля и PIN."""
    await logged_admin(panel, db_session)
    user = await create_user(db_session)
    await give_pin(db_session, user.id, "918273")

    for path in (f"user/details/{user.id}", "user/list"):
        page = await panel.get(f"{BASE}/{path}")
        assert page.status_code == 200, page.text
        assert user.email in page.text
        assert user.password_hash not in page.text, path
        assert "918273" not in page.text, path
        assert "password_hash" not in page.text.lower().replace(" ", "_"), path
        assert "checkin_pin" not in page.text.lower().replace(" ", "_"), path


async def test_side_effect_statuses_not_in_forms(
    panel: AsyncClient, client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-41: статус KYC и статус брони не правятся в форме (только кнопками)."""
    booking_id, _ = await paid_vehicle_booking(db_session, client)
    _, headers = await user_with_headers(db_session, client)
    kyc = await submit(client, headers)
    await logged_admin(panel, db_session)

    kyc_page = await panel.get(f"{BASE}/kyc-verification/edit/{kyc['id']}")
    assert kyc_page.status_code == 200, kyc_page.text
    for field in ("status", "reviewed_by", "checked_at", "rejection_reason"):
        assert f'name="{field}"' not in kyc_page.text, field

    booking_page = await panel.get(f"{BASE}/booking/edit/{booking_id}")
    assert booking_page.status_code == 200, booking_page.text
    assert 'name="status"' not in booking_page.text


# --- 5. Кнопки-действия через сервисы ----------------------------------------


async def test_block_action(
    panel: AsyncClient, client: AsyncClient, db_session: AsyncSession, redis: Redis
) -> None:
    """R-41, R-28: кнопка block → blocked, аудит user_blocked от admin, ключ денилиста в Redis,
    refresh отозван, вход через API запрещён."""
    admin = await logged_admin(panel, db_session)
    target = await create_user(db_session)
    tokens = await login(client, target.email)

    response = await panel.get(f"{BASE}/user/action/block", params={"pks": str(target.id)})

    assert response.status_code == 302, response.text
    assert response.headers["location"].endswith(f"/user/details/{target.id}")
    page = await follow(panel, response)
    assert f"#{target.id}: blocked" in page.text
    assert (await fresh(db_session, User, target.id)).status == UserStatus.BLOCKED
    [row] = await audit_rows(db_session, "user_blocked", "user")
    assert (row.actor_id, row.entity_id) == (admin.id, target.id)
    assert await redis.exists(f"auth:denylist:{target.id}")
    refresh = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh.status_code == 401, refresh.text
    relogin = await client.post(
        "/auth/login", json={"email": target.email, "password": DEFAULT_PASSWORD}
    )
    assert relogin.status_code == 403, relogin.text


async def test_block_action_several_users(panel: AsyncClient, db_session: AsyncSession) -> None:
    """R-41 (граница): кнопка на несколько записей → 302 на список, заблокированы все, а
    посторонний пользователь — нет."""
    await logged_admin(panel, db_session)
    first = await create_user(db_session)
    second = await create_user(db_session)
    bystander = await create_user(db_session)

    response = await panel.get(
        f"{BASE}/user/action/block", params={"pks": f"{first.id},{second.id}"}
    )

    assert response.status_code == 302, response.text
    assert response.headers["location"].endswith("/user/list")
    for user in (first, second):
        assert (await fresh(db_session, User, user.id)).status == UserStatus.BLOCKED
    assert (await fresh(db_session, User, bystander.id)).status == UserStatus.ACTIVE
    assert len(await audit_rows(db_session, "user_blocked", "user")) == 2


async def test_unblock_action(panel: AsyncClient, db_session: AsyncSession, redis: Redis) -> None:
    """R-41, R-28: кнопка unblock → active, аудит user_unblocked, ключ денилиста снят."""
    admin = await logged_admin(panel, db_session)
    target = await create_user(db_session)
    assert (await panel.get(f"{BASE}/user/action/block?pks={target.id}")).status_code == 302

    response = await panel.get(f"{BASE}/user/action/unblock?pks={target.id}")

    assert response.status_code == 302, response.text
    assert (await fresh(db_session, User, target.id)).status == UserStatus.ACTIVE
    [row] = await audit_rows(db_session, "user_unblocked", "user")
    assert (row.actor_id, row.entity_id) == (admin.id, target.id)
    assert not await redis.exists(f"auth:denylist:{target.id}")


async def test_block_action_unknown_user(panel: AsyncClient, db_session: AsyncSession) -> None:
    """R-41 (нарушение): блокировка несуществующего пользователя — flash с ошибкой, аудита нет."""
    await logged_admin(panel, db_session)

    response = await panel.get(f"{BASE}/user/action/block?pks=999999")

    assert response.status_code == 302, response.text
    page = await panel.get(f"{BASE}/user/list")
    assert "#999999: User not found" in page.text
    assert await audit_rows(db_session, "user_blocked") == []


async def test_approve_kyc_action(
    panel: AsyncClient, client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-41, R-23, R-39, R-28: кнопка approve → KYC и users.kyc_status approved, выдан PIN
    из 6 цифр, уведомление kyc_approved без PIN, аудит kyc_approved от admin."""
    admin = await logged_admin(panel, db_session)
    user, headers = await user_with_headers(db_session, client)
    kyc = await submit(client, headers)

    response = await panel.get(f"{BASE}/kyc-verification/action/approve?pks={kyc['id']}")

    assert response.status_code == 302, response.text
    page = await follow(panel, response)
    assert f"#{kyc['id']}: approved" in page.text
    row = await fresh(db_session, KycVerification, kyc["id"])
    assert (row.status, row.reviewed_by) == (KycStatus.APPROVED, admin.id)
    owner = await fresh(db_session, User, user.id)
    assert owner.kyc_status == KycStatus.APPROVED
    assert owner.checkin_pin is not None
    assert len(owner.checkin_pin) == 6 and owner.checkin_pin.isdigit()
    notes = (
        await db_session.scalars(select(Notification).where(Notification.user_id == user.id))
    ).all()
    assert [(n.type, n.payload) for n in notes] == [("kyc_approved", {"kyc_id": kyc["id"]})]
    assert owner.checkin_pin not in json.dumps(notes[0].payload)
    [audit] = await audit_rows(db_session, "kyc_approved", "kyc_verification")
    assert (audit.actor_id, audit.entity_id) == (admin.id, kyc["id"])


async def test_approve_kyc_already_decided(
    panel: AsyncClient, client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-41 (граница): повторное approve уже решённой заявки — flash с ошибкой, PIN тот же,
    второго уведомления и аудита нет."""
    await logged_admin(panel, db_session)
    user, headers = await user_with_headers(db_session, client)
    kyc = await submit(client, headers)
    url = f"{BASE}/kyc-verification/action/approve?pks={kyc['id']}"
    await follow(panel, await panel.get(url))  # показать flash первого решения
    pin = (await fresh(db_session, User, user.id)).checkin_pin

    page = await follow(panel, await panel.get(url))

    assert "already decided" in page.text
    assert (await fresh(db_session, User, user.id)).checkin_pin == pin
    assert len(await notifications_of(db_session, user.id)) == 1
    assert len(await audit_rows(db_session, "kyc_approved")) == 1


async def test_reject_kyc_requires_reason(
    panel: AsyncClient, client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-41: reject без причины → 200 с формой причины; заявка остаётся pending."""
    await logged_admin(panel, db_session)
    user, headers = await user_with_headers(db_session, client)
    kyc = await submit(client, headers)

    for query in (f"pks={kyc['id']}", f"pks={kyc['id']}&reason=%20%20"):
        response = await panel.get(f"{BASE}/kyc-verification/action/reject?{query}")
        assert response.status_code == 200, response.text
        assert 'name="reason"' in response.text

    assert (await fresh(db_session, KycVerification, kyc["id"])).status == KycStatus.PENDING
    assert (await fresh(db_session, User, user.id)).kyc_status == KycStatus.PENDING
    assert await notifications_of(db_session, user.id) == []
    assert await audit_rows(db_session, "kyc_rejected") == []


async def test_reject_kyc_with_reason(
    panel: AsyncClient, client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-41, R-39, R-28: reject с причиной → rejected, kyc_status rejected, PIN не выдан,
    уведомление kyc_rejected с причиной, аудит kyc_rejected с причиной."""
    admin = await logged_admin(panel, db_session)
    user, headers = await user_with_headers(db_session, client)
    kyc = await submit(client, headers)

    response = await panel.get(
        f"{BASE}/kyc-verification/action/reject",
        params={"pks": str(kyc["id"]), "reason": "Фото размыто"},
    )

    assert response.status_code == 302, response.text
    row = await fresh(db_session, KycVerification, kyc["id"])
    assert (row.status, row.rejection_reason, row.reviewed_by) == (
        KycStatus.REJECTED,
        "Фото размыто",
        admin.id,
    )
    owner = await fresh(db_session, User, user.id)
    assert (owner.kyc_status, owner.checkin_pin) == (KycStatus.REJECTED, None)
    assert await notifications_of(db_session, user.id) == [
        ("kyc_rejected", {"kyc_id": kyc["id"], "reason": "Фото размыто"})
    ]
    [audit] = await audit_rows(db_session, "kyc_rejected", "kyc_verification")
    assert (audit.actor_id, audit.details) == (admin.id, {"reason": "Фото размыто"})


async def paid_vehicle_booking(db_session: AsyncSession, client: AsyncClient) -> tuple[int, int]:
    """Оплаченная (confirmed) аренда машины 5000 × 2 дня; возвращает id брони и владельца."""
    owner, _, vehicle_id = await make_vehicle(db_session, client, price="5000.00")
    guest, headers = await guest_with_headers(db_session, client)
    booking = await rent(client, headers, rent_body(vehicle_id, 1, 3))
    assert (await pay(client, headers, booking["id"], "pay-vehicle")).status_code == 200
    await give_pin(db_session, guest.id)
    return int(booking["id"]), owner.id


async def test_complete_booking_action_pays_owner(
    panel: AsyncClient, client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-41, R-35, R-39: кнопка complete у аренды машины после end_date → completed,
    выплата владельцу 9000 (10000 − 10%) со статусом paid, уведомление payout_paid."""
    booking_id, owner_id = await paid_vehicle_booking(db_session, client)
    await end_stay_period(db_session, booking_id)
    await logged_admin(panel, db_session)

    response = await panel.get(f"{BASE}/booking/action/complete?pks={booking_id}")

    assert response.status_code == 302, response.text
    page = await follow(panel, response)
    assert f"#{booking_id}: completed" in page.text
    booking = await fresh(db_session, Booking, booking_id)
    assert booking.status == BookingStatus.COMPLETED
    [payout] = await payouts_of(db_session, booking_id)
    assert (payout.recipient_id, str(payout.amount), payout.status) == (
        owner_id,
        "9000.00",
        PayoutStatus.PAID,
    )
    assert ("payout_paid", {"booking_id": booking_id}) in await notifications_of(
        db_session, owner_id
    )


async def test_complete_booking_before_end_date(
    panel: AsyncClient, client: AsyncClient, db_session: AsyncSession
) -> None:
    """R-41, R-35 (нарушение): complete до end_date — flash с ошибкой, бронь confirmed,
    выплаты нет."""
    booking_id, _ = await paid_vehicle_booking(db_session, client)
    await logged_admin(panel, db_session)

    response = await panel.get(f"{BASE}/booking/action/complete?pks={booking_id}")

    assert response.status_code == 302, response.text
    page = await follow(panel, response)
    assert f"#{booking_id}: completed" not in page.text
    assert f"#{booking_id}:" in page.text
    assert (await fresh(db_session, Booking, booking_id)).status == BookingStatus.CONFIRMED
    assert await payouts_of(db_session, booking_id) == []


# --- 6. Аудит изменений через панель -----------------------------------------


async def test_panel_crud_audited(panel: AsyncClient, db_session: AsyncSession) -> None:
    """R-41, R-28: создание, правка и удаление через панель → audit_logs admin_panel_create /
    admin_panel_update / admin_panel_delete с entity_type = identity и actor_id = admin."""
    admin = await logged_admin(panel, db_session)

    assert (await panel.post(f"{BASE}/tag/create", data={"name": "панель"})).status_code == 302
    tag = await db_session.scalar(select(Tag).where(Tag.name == "панель"))
    assert tag is not None
    edit = await panel.post(f"{BASE}/tag/edit/{tag.id}", data={"name": "панель-2"})
    assert edit.status_code == 302, edit.text
    delete = await panel.delete(f"{BASE}/tag/delete", params={"pks": str(tag.id)})
    assert delete.status_code == 200, delete.text
    assert await fresh(db_session, Tag, tag.id) is None

    expected = {
        "admin_panel_create": {"name": "панель"},
        "admin_panel_update": {"name": "панель-2"},
    }
    for action, changes in expected.items():
        [row] = await audit_rows(db_session, action, "tag")
        assert (row.actor_id, row.entity_id) == (admin.id, tag.id)
        assert row.details["changes"] == changes
    [deleted] = await audit_rows(db_session, "admin_panel_delete", "tag")
    assert (deleted.actor_id, deleted.entity_id) == (admin.id, tag.id)


async def test_failed_change_not_audited(panel: AsyncClient, db_session: AsyncSession) -> None:
    """R-41 (граница): отклонённое создание (400) не пишет admin_panel_create."""
    await logged_admin(panel, db_session)
    data = user_form("fail@example.kg")
    del data["new_password"]

    assert (await panel.post(f"{BASE}/user/create", data=data)).status_code == 400
    assert await audit_rows(db_session, "admin_panel_create") == []


async def test_user_audit_has_no_secrets(panel: AsyncClient, db_session: AsyncSession) -> None:
    """R-41, R-28: аудит создания и правки пользователя без new_password, password_hash и PIN."""
    admin = await logged_admin(panel, db_session)
    assert (
        await panel.post(f"{BASE}/user/create", data=user_form("audit@example.kg"))
    ).status_code == 302
    user = await db_session.scalar(select(User).where(User.email == "audit@example.kg"))
    assert user is not None
    await give_pin(db_session, user.id, "918273")
    edit = await panel.post(
        f"{BASE}/user/edit/{user.id}",
        data=user_edit_form(user, first_name="Changed", new_password="Another123!"),
    )
    assert edit.status_code == 302, edit.text
    user = await fresh(db_session, User, user.id)

    [created] = await audit_rows(db_session, "admin_panel_create", "user")
    [updated] = await audit_rows(db_session, "admin_panel_update", "user")
    for row in (created, updated):
        assert (row.actor_id, row.entity_id) == (admin.id, user.id)
        changes = row.details["changes"]
        assert not {"new_password", "password_hash", "checkin_pin"} & changes.keys()
        dump = json.dumps(row.details, ensure_ascii=False)
        for secret in (NEW_PASSWORD, "Another123!", user.password_hash, "918273"):
            assert secret not in dump
    assert created.details["changes"]["email"] == "audit@example.kg"
    assert updated.details["changes"]["first_name"] == "Changed"


# --- 7. host-profile и blocked_dates -----------------------------------------


async def test_host_profile_blocked_dates(panel: AsyncClient, db_session: AsyncSession) -> None:
    """R-41: blocked_dates — список YYYY-MM-DD; в БД сохраняются даты, страница правки
    открывается и показывает их, правка заменяет список (в том числе на пустой)."""
    await logged_admin(panel, db_session)
    host = await create_user(db_session)
    region = await first_region(db_session)

    created = await panel.post(
        f"{BASE}/host-profile/create",
        data=host_form(host.id, region.id, ["2026-12-01", "2026-12-31"]),
    )
    assert created.status_code == 302, created.text
    profile = await fresh(db_session, HostProfile, host.id)
    assert profile is not None
    assert sorted(profile.blocked_dates) == [date(2026, 12, 1), date(2026, 12, 31)]

    page = await panel.get(f"{BASE}/host-profile/edit/{host.id}")
    assert page.status_code == 200, page.text
    assert "2026-12-01" in page.text and "2026-12-31" in page.text

    edited = await panel.post(
        f"{BASE}/host-profile/edit/{host.id}", data=host_form(host.id, region.id, ["2027-01-15"])
    )
    assert edited.status_code == 302, edited.text
    assert (await fresh(db_session, HostProfile, host.id)).blocked_dates == [date(2027, 1, 15)]

    cleared = await panel.post(
        f"{BASE}/host-profile/edit/{host.id}", data=host_form(host.id, region.id, [])
    )
    assert cleared.status_code == 302, cleared.text
    assert (await fresh(db_session, HostProfile, host.id)).blocked_dates == []


@pytest.mark.parametrize("bad", ["2026-13-01", "01.12.2026", "завтра"])
async def test_host_profile_invalid_date(
    panel: AsyncClient, db_session: AsyncSession, bad: str
) -> None:
    """R-41: неверная дата в blocked_dates → 400 с ошибкой, профиль не создан."""
    await logged_admin(panel, db_session)
    host = await create_user(db_session)
    region = await first_region(db_session)

    response = await panel.post(
        f"{BASE}/host-profile/create", data=host_form(host.id, region.id, ["2026-12-01", bad])
    )

    assert response.status_code == 400, response.text
    assert "Dates must be YYYY-MM-DD" in response.text
    assert await fresh(db_session, HostProfile, host.id) is None


async def test_host_profile_check_constraint(panel: AsyncClient, db_session: AsyncSession) -> None:
    """R-41 (ограничения БД действуют): capacity = 0 нарушает CHECK → 400, профиля нет."""
    await logged_admin(panel, db_session)
    host = await create_user(db_session)
    region = await first_region(db_session)
    data = host_form(host.id, region.id, [])
    data["capacity"] = "0"

    response = await panel.post(f"{BASE}/host-profile/create", data=data)

    assert response.status_code == 400, response.text
    assert await fresh(db_session, HostProfile, host.id) is None
