"""Админ-панель (SQLAdmin) на /admin-panel: CRUD всех таблиц, вход только для admin (R-41).

Прямая правка таблиц обходит сервисы, поэтому:
- статусы с побочными эффектами (блокировка, решение KYC, закрытие брони) в формах только
  для чтения и меняются кнопками-действиями, которые вызывают сервисы модулей;
- настройки меняются через сервис настроек (проверка формата, аудит R-28);
- каждое создание, правку и удаление через панель SQLAdmin пишет в audit_logs.
"""

from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from redis.asyncio import Redis
from sqladmin import Admin, ModelView, action
from sqladmin.audit import AuditEntry, DBAuditBackend
from sqladmin.authentication import AuthenticationBackend
from sqladmin.fields import Select2TagsField
from sqladmin.flash import Flash
from sqladmin.forms import ModelConverter, converts
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import ColumnProperty
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from wtforms import PasswordField, ValidationError, validators
from wtforms.form import BaseForm

from app.core.audit import AuditLog
from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.security import (
    hash_password_async,
    verify_dummy_password_async,
    verify_password_async,
)
from app.modules.activities.models import Activity
from app.modules.admin import service as admin_service
from app.modules.admin.models import Place, Region, Setting, Tag
from app.modules.bookings import service as bookings_service
from app.modules.bookings.models import Booking, BookingStatus
from app.modules.chat.models import Conversation, Message
from app.modules.favorites.models import Favorite
from app.modules.guests.models import GuestProfile
from app.modules.hosts.models import HostProfile
from app.modules.kyc import service as kyc_service
from app.modules.kyc.models import KycVerification
from app.modules.notifications.models import Notification
from app.modules.payments.models import BookingActivity, Payment, Payout, Refund
from app.modules.reviews.models import Review
from app.modules.support.models import SupportTicket
from app.modules.users import service as users_service
from app.modules.users.models import User, UserRole, UserStatus
from app.modules.vehicles.models import Vehicle

BASE_URL = "/admin-panel"
TEMPLATES_DIR = Path(__file__).parent / "templates"
SESSION_TTL_SEC = 8 * 3600
PASSWORD_MIN_LENGTH = 8  # как Password в users/schemas.py
# Не попадают в журнал: хеш пароля, сам пароль и PIN (R-23).
SECRET_FIELDS = {"new_password", "password_hash", "checkin_pin"}


def _is_active_admin(user: User | None) -> bool:
    return user is not None and user.role == UserRole.ADMIN and user.status == UserStatus.ACTIVE


class PanelAuth(AuthenticationBackend):
    """Вход по email и паролю; роль и статус перепроверяются на каждом запросе."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        # strict: cookie не уходит с чужих сайтов, а кнопки-действия SQLAdmin — это GET-ссылки.
        super().__init__(
            get_settings().secret_key.get_secret_value(),
            same_site="strict",
            max_age=SESSION_TTL_SEC,
        )
        self.session_factory = session_factory

    async def login(self, request: Request) -> bool:
        form = await request.form()
        email = str(form.get("username", "")).strip().lower()
        password = str(form.get("password", ""))
        async with self.session_factory() as session:
            user = await session.scalar(select(User).where(func.lower(User.email) == email))
        if user is None:
            await verify_dummy_password_async(password)
            return False
        if not await verify_password_async(password, user.password_hash):
            return False
        if not _is_active_admin(user):
            return False
        request.session.clear()
        request.session["user_id"] = user.id
        return True

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        user_id = request.session.get("user_id")
        if not isinstance(user_id, int):
            return False
        async with self.session_factory() as session:
            user = await session.get(User, user_id)
        if not _is_active_admin(user):
            request.session.clear()
            return False
        return True


class PanelAudit(DBAuditBackend):
    """Изменения через панель — в audit_logs (отдельной транзакцией после изменения)."""

    def build_row(self, entry: AuditEntry, actor: Any, request: Request) -> AuditLog:
        changes = {k: v for k, v in (entry.changes or {}).items() if k not in SECRET_FIELDS}
        return AuditLog(
            actor_id=actor,
            action=f"admin_panel_{entry.action}",
            entity_type=entry.identity,
            entity_id=int(entry.pk) if entry.pk and entry.pk.isdigit() else None,
            details={"pk": entry.pk, "changes": jsonable_encoder(changes)},
        )

# TODO: что делает этот класс?
class PanelConverter(ModelConverter):
    @converts("Enum")  # type: ignore[untyped-decorator]  # T_CC в sqladmin без типов
    def conv_enum(self, model: type, prop: ColumnProperty[Any], kwargs: dict[str, Any]) -> Any:
        # В БД значения enum, а не имена: без этого форма правки не выбирает текущее значение.
        kwargs.setdefault("coerce", lambda v: v.value if hasattr(v, "value") else str(v))
        return super().conv_enum(model, prop, kwargs)


class PanelView(ModelView):
    # Иначе SQLAdmin прячет из форм и внешние ключи (user_id, region_id …).
    form_include_pk = True
    form_excluded_columns = ["id", "created_at", "updated_at"]
    form_converter = PanelConverter
    page_size = 50


def _session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory

#TODO: что делает эта функция?
async def _run_action(
    view: ModelView,
    request: Request,
    call: Callable[[AsyncSession, int, int], Awaitable[object]],
    done: str,
) -> Response:
    """Вызывает сервис для каждой выбранной записи; итог — flash-сообщением."""
    actor_id: int = request.session["user_id"]
    pks = [pk for pk in request.query_params.get("pks", "").split(",") if pk.isdigit()]
    for pk in pks:
        try:
            async with _session_factory(request)() as session:
                await call(session, actor_id, int(pk))
        except DomainError as exc:
            Flash.error(request, f"#{pk}: {exc.message}")
        else:
            Flash.success(request, f"#{pk}: {done}")
    if len(pks) == 1:
        url = request.url_for("admin:details", identity=view.identity, pk=pks[0])
    else:
        url = request.url_for("admin:list", identity=view.identity)
    return RedirectResponse(url, status_code=302)


# --- Справочники и настройки --------------------------------------------------


class RegionAdmin(PanelView, model=Region):
    can_delete = False  # регион используется хозяевами, местами и машинами
    column_list = [Region.id, Region.name]
    column_searchable_list = [Region.name]


class TagAdmin(PanelView, model=Tag):
    column_list = [Tag.id, Tag.name]
    column_searchable_list = [Tag.name]

# TODO: зачем поле slug?
class PlaceAdmin(PanelView, model=Place):
    column_list = [Place.id, Place.slug, Place.name, Place.region_id]
    column_searchable_list = [Place.slug, Place.name]

# TODO: что делает этот класс? Что значит "ключи читает код: новый ключ бесполезен, удаление известного ломает сервисы"? какой ключ?
class SettingAdmin(PanelView, model=Setting):
    # Ключи читает код: новый ключ бесполезен, удаление известного ломает сервисы.
    can_create = False
    can_delete = False
    column_list = [Setting.key, Setting.value, Setting.updated_by, Setting.updated_at]
    form_columns = [Setting.value]

    async def update_model(self, request: Request, pk: str, data: dict[str, Any]) -> Any:
        # Проверка формата и аудит (R-28) — в сервисе, как у PUT /admin/settings/{key}.
        async with _session_factory(request)() as session:
            return await admin_service.update_setting(
                session, actor_id=request.session["user_id"], key=pk, value=data["value"]
            )


class AuditLogAdmin(PanelView, model=AuditLog):
    # Журнал (R-28) только для чтения: иначе его можно переписать задним числом.
    can_create = False
    can_edit = False
    can_delete = False
    column_list = [
        AuditLog.id,
        AuditLog.created_at,
        AuditLog.actor_id,
        AuditLog.action,
        AuditLog.entity_type,
        AuditLog.entity_id,
    ]
    column_searchable_list = [AuditLog.action, AuditLog.entity_type]
    column_default_sort = [(AuditLog.id, True)]


# --- Пользователи и KYC ------------------------------------------------------


class UserAdmin(PanelView, model=User):
    column_list = [
        User.id,
        User.email,
        User.phone,
        User.first_name,
        User.last_name,
        User.role,
        User.status,
        User.kyc_status,
    ]
    column_details_exclude_list = [User.password_hash, User.checkin_pin]
    column_export_exclude_list = [User.password_hash, User.checkin_pin]
    column_searchable_list = [User.email, User.phone, User.last_name]
    # status — кнопками (аудит, отзыв токенов); kyc_status — решением по KYC (PIN, R-23).
    form_excluded_columns = [
        "id",
        "created_at",
        "updated_at",
        "password_hash",
        "checkin_pin",
        "status",
        "kyc_status",
    ]

    async def scaffold_form(self, rules: list[str] | None = None) -> Any:
        form = await super().scaffold_form(rules)
        form.new_password = PasswordField(
            "New password",
            validators=[validators.Optional(), validators.Length(PASSWORD_MIN_LENGTH, 128)],
            description="Обязателен при создании. При правке пусто — пароль не меняется.",
        )
        return form

    async def on_model_change(
        self, data: dict[str, Any], model: Any, is_created: bool, request: Request
    ) -> None:
        password = data.pop("new_password", None)
        if password:
            data["password_hash"] = await hash_password_async(password)
        elif is_created:
            raise ValueError("New password is required")

    @action("block", "Block", "Block selected users?")
    async def block(self, request: Request) -> Response:
        return await _run_action(
            self, request, _status_setter(request, UserStatus.BLOCKED), "blocked"
        )

    @action("unblock", "Unblock", "Unblock selected users?")
    async def unblock(self, request: Request) -> Response:
        return await _run_action(
            self, request, _status_setter(request, UserStatus.ACTIVE), "unblocked"
        )


def _status_setter(
    request: Request, status: UserStatus
) -> Callable[[AsyncSession, int, int], Awaitable[object]]:
    redis: Redis = request.app.state.redis

    async def call(session: AsyncSession, actor_id: int, user_id: int) -> object:
        return await users_service.set_user_status(
            session, redis, actor_id=actor_id, user_id=user_id, status=status
        )

    return call


class KycAdmin(PanelView, model=KycVerification):
    name = "KYC verification"
    column_list = [
        KycVerification.id,
        KycVerification.user_id,
        KycVerification.resident_type,
        KycVerification.status,
        KycVerification.submitted_at,
        KycVerification.checked_at,
    ]
    column_default_sort = [(KycVerification.id, True)]
    # Решение — кнопками: копия статуса в users, PIN (R-23), уведомление (R-39), аудит (R-28).
    form_excluded_columns = [
        "id",
        "status",
        "reviewed_by",
        "checked_at",
        "rejection_reason",
    ]

    @action("approve", "Approve", "Approve selected KYC verifications?")
    async def approve(self, request: Request) -> Response:
        async def call(session: AsyncSession, actor_id: int, kyc_id: int) -> object:
            return await kyc_service.decide(
                session, reviewer_id=actor_id, kyc_id=kyc_id, approve=True
            )

        return await _run_action(self, request, call, "approved")

    @action("reject", "Reject")
    async def reject(self, request: Request) -> Response:
        reason = request.query_params.get("reason", "").strip()
        if not reason:
            return await self.templates.TemplateResponse(
                request,
                "kyc_reject.html",
                {"pks": request.query_params.get("pks", ""), "title": "Reject KYC"},
            )

        async def call(session: AsyncSession, actor_id: int, kyc_id: int) -> object:
            return await kyc_service.decide(
                session, reviewer_id=actor_id, kyc_id=kyc_id, approve=False, reason=reason
            )

        return await _run_action(self, request, call, "rejected")


# --- Профили, занятия, машины ------------------------------------------------


class GuestProfileAdmin(PanelView, model=GuestProfile):
    column_list = [GuestProfile.user_id, GuestProfile.country, GuestProfile.birth_date]

# TODO: Зачем этот класс?
class DateTagsField(Select2TagsField):
    """DATE[] в форме — строки YYYY-MM-DD: виджет тегов сериализует значения в JSON."""

    def process_data(self, value: list[date] | None) -> None:
        self.data = [d.isoformat() for d in value or []]

    def pre_validate(self, form: BaseForm) -> None:
        try:
            for value in self.data:
                date.fromisoformat(value)
        except ValueError as exc:
            raise ValidationError("Dates must be YYYY-MM-DD") from exc


class HostProfileAdmin(PanelView, model=HostProfile):
    column_list = [
        HostProfile.user_id,
        HostProfile.region_id,
        HostProfile.settlement,
        HostProfile.capacity,
        HostProfile.price_per_person_night,
        HostProfile.is_published,
    ]
    column_searchable_list = [HostProfile.settlement]
    form_overrides = {"blocked_dates": DateTagsField}

    async def on_model_change(
        self, data: dict[str, Any], model: Any, is_created: bool, request: Request
    ) -> None:
        if "blocked_dates" in data:
            data["blocked_dates"] = [
                date.fromisoformat(str(d)) for d in data["blocked_dates"] or []
            ]


class ActivityAdmin(PanelView, model=Activity):
    column_list = [
        Activity.id,
        Activity.host_id,
        Activity.name,
        Activity.price_per_person,
        Activity.is_active,
    ]
    column_searchable_list = [Activity.name]


class VehicleAdmin(PanelView, model=Vehicle):
    column_list = [
        Vehicle.id,
        Vehicle.owner_id,
        Vehicle.region_id,
        Vehicle.model,
        Vehicle.price_per_day,
        Vehicle.is_published,
    ]
    column_searchable_list = [Vehicle.model]


# --- Брони и деньги (имитация, R-30) ----------------------------------------


class BookingAdmin(PanelView, model=Booking):
    column_list = [
        Booking.id,
        Booking.type,
        Booking.guest_id,
        Booking.host_id,
        Booking.vehicle_id,
        Booking.start_date,
        Booking.end_date,
        Booking.status,
    ]
    column_default_sort = [(Booking.id, True)]
    # Закрытие — кнопками: выплаты (R-35) и уведомления (R-39) делает сервис.
    form_excluded_columns = ["id", "created_at", "updated_at", "status"]

    @action("complete", "Complete", "Mark selected bookings as completed?")
    async def complete(self, request: Request) -> Response:
        return await _run_action(self, request, _closer(BookingStatus.COMPLETED), "completed")

    @action("no_show", "No-show", "Mark selected bookings as no-show?")
    async def no_show(self, request: Request) -> Response:
        return await _run_action(self, request, _closer(BookingStatus.NO_SHOW), "no-show")


def _closer(outcome: BookingStatus) -> Callable[[AsyncSession, int, int], Awaitable[object]]:
    async def call(session: AsyncSession, actor_id: int, booking_id: int) -> object:
        return await bookings_service.close_booking(session, booking_id, outcome)

    return call


class PaymentAdmin(PanelView, model=Payment):
    column_list = [
        Payment.id,
        Payment.booking_id,
        Payment.payer_id,
        Payment.amount,
        Payment.commission_amount,
        Payment.status,
        Payment.payout_id,
    ]
    column_default_sort = [(Payment.id, True)]


class BookingActivityAdmin(PanelView, model=BookingActivity):
    column_list = [
        BookingActivity.id,
        BookingActivity.booking_id,
        BookingActivity.activity_name,
        BookingActivity.persons,
        BookingActivity.amount,
        BookingActivity.payment_id,
    ]


class PayoutAdmin(PanelView, model=Payout):
    column_list = [
        Payout.id,
        Payout.booking_id,
        Payout.recipient_id,
        Payout.amount,
        Payout.status,
        Payout.paid_at,
    ]
    column_default_sort = [(Payout.id, True)]


class RefundAdmin(PanelView, model=Refund):
    column_list = [Refund.id, Refund.payment_id, Refund.amount, Refund.status, Refund.processed_at]
    column_default_sort = [(Refund.id, True)]


# --- Общение и прочее --------------------------------------------------------


class ReviewAdmin(PanelView, model=Review):
    column_list = [
        Review.id,
        Review.booking_id,
        Review.author_id,
        Review.target_user_id,
        Review.rating,
        Review.status,
    ]


class ConversationAdmin(PanelView, model=Conversation):
    column_list = [
        Conversation.id,
        Conversation.guest_id,
        Conversation.provider_id,
        Conversation.last_message_at,
    ]


class MessageAdmin(PanelView, model=Message):
    column_list = [
        Message.id,
        Message.conversation_id,
        Message.sender_id,
        Message.original_text,
        Message.is_flagged,
        Message.created_at,
    ]
    column_default_sort = [(Message.id, True)]


class SupportTicketAdmin(PanelView, model=SupportTicket):
    column_list = [
        SupportTicket.id,
        SupportTicket.user_id,
        SupportTicket.booking_id,
        SupportTicket.category,
        SupportTicket.status,
        SupportTicket.assigned_to,
    ]
    column_default_sort = [(SupportTicket.id, True)]

# TODO: по идее не нужен в админке, разве что для статистики. можно пока оставить.
class FavoriteAdmin(PanelView, model=Favorite):
    column_list = [
        Favorite.id,
        Favorite.user_id,
        Favorite.host_id,
        Favorite.place_id,
        Favorite.vehicle_id,
    ]


class NotificationAdmin(PanelView, model=Notification):
    column_list = [
        Notification.id,
        Notification.user_id,
        Notification.type,
        Notification.channel,
        Notification.read_at,
        Notification.created_at,
    ]
    column_default_sort = [(Notification.id, True)]


VIEWS: list[type[ModelView]] = [
    UserAdmin,
    KycAdmin,
    GuestProfileAdmin,
    HostProfileAdmin,
    ActivityAdmin,
    VehicleAdmin,
    BookingAdmin,
    BookingActivityAdmin,
    PaymentAdmin,
    PayoutAdmin,
    RefundAdmin,
    ReviewAdmin,
    ConversationAdmin,
    MessageAdmin,
    SupportTicketAdmin,
    FavoriteAdmin,
    NotificationAdmin,
    RegionAdmin,
    TagAdmin,
    PlaceAdmin,
    SettingAdmin,
    AuditLogAdmin,
]


def mount_admin_panel(app: FastAPI, engine: AsyncEngine, redis: Redis) -> Admin:
    # Отдельная фабрика: SQLAdmin меняет настройки (autoflush) у переданной ему фабрики.
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    admin = Admin(
        app,
        engine=engine,
        base_url=BASE_URL,
        title="Konok admin",
        authentication_backend=PanelAuth(session_factory),
        audit_backend=PanelAudit(session_factory),
        templates_dir=str(TEMPLATES_DIR),
    )
    admin.admin.state.session_factory = session_factory
    admin.admin.state.redis = redis
    for view in VIEWS:
        admin.add_view(view)
    return admin
