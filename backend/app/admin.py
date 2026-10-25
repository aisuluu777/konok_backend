"""Админ-панель SQLAdmin (/admin). Входит только пользователь с role=admin и is_active.

Каждое создание, правка и удаление через панель пишется в audit_logs.
"""

from decimal import Decimal
from typing import Any

from fastapi import FastAPI
from markupsafe import Markup
from sqladmin import Admin, BaseView, ModelView, action, expose
from sqladmin.authentication import AuthenticationBackend
from sqladmin.helpers import get_object_identifier
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request
from starlette.responses import FileResponse, RedirectResponse, Response
from wtforms import MultipleFileField

from app import rules, storage
from app.config import get_settings
from app.errors import AppError, conflict, not_found
from app.models import (
    Activity,
    ActivityPhoto,
    AuditLog,
    Booking,
    BookingStatus,
    Complaint,
    HostBlockedDate,
    HostPhoto,
    HostProfile,
    Locality,
    Message,
    Payment,
    PaymentStatus,
    Place,
    PlacePhoto,
    Region,
    Review,
    Setting,
    SupportMessage,
    SupportTicket,
    Tag,
    User,
    UserRole,
    Vehicle,
    VehicleBlockedDate,
    VehiclePhoto,
)
from app.notify import notify
from app.payments import get_payment_provider
from app.routers.bookings import provider_id
from app.security import verify_password


def write_audit(
    session: AsyncSession,
    admin_id: int | None,
    action: str,
    entity_type: str,
    entity_id: int | None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            admin_id=admin_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
        )
    )


async def emergency_cancel(
    session: AsyncSession, booking_id: int, admin_id: int, reason_valid: bool
) -> Booking:
    """Экстренная отмена оплаченной брони admin: гостю полный возврат, хозяину ничего.

    Причина неуважительная — хозяину / авто скрытая оценка 1 (считается по
    admin_reason_valid) и даты брони закрываются в календаре.
    """
    booking = await session.scalar(
        select(Booking).where(Booking.id == booking_id).with_for_update()
    )
    if booking is None:
        raise not_found("Бронь не найдена")
    if booking.status != BookingStatus.CONFIRMED:
        raise conflict("Экстренно отменить можно только оплаченную бронь до заселения")
    provider = get_payment_provider()
    total = sum((p.amount for p in booking.payments if p.status == PaymentStatus.PAID), Decimal(0))
    for payment in booking.payments:
        if payment.status == PaymentStatus.PAID:
            await provider.refund(payment.pg_order_id, payment.amount)
            payment.refunded_amount = payment.amount
            payment.commission_amount = Decimal(0)
        elif payment.status == PaymentStatus.PENDING:
            payment.status = PaymentStatus.FAILED
    booking.status = BookingStatus.CANCELLED_BY_ADMIN
    booking.cancelled_at = rules.now_bishkek()
    booking.refund_amount = total
    booking.admin_reason_valid = reason_valid
    for user_id in (booking.guest_id, await provider_id(session, booking)):
        notify(session, user_id, "booking_cancelled", {"booking_id": booking.id, "by": "admin"})
    if not reason_valid:
        # Даты отменённой брони закрываются в календаре хозяина / авто.
        nights = rules.days(booking.start_date, booking.end_date)
        if booking.host_id is not None:
            block = insert(HostBlockedDate).values(
                [{"host_id": booking.host_id, "date": d} for d in nights]
            )
        else:
            block = insert(VehicleBlockedDate).values(
                [{"vehicle_id": booking.vehicle_id, "date": d} for d in nights]
            )
        await session.execute(block.on_conflict_do_nothing())
    write_audit(
        session,
        admin_id,
        "booking_emergency_cancel",
        "booking",
        booking.id,
        {"reason_valid": reason_valid, "refund": str(total)},
    )
    await session.commit()
    return booking


# --- Вход ----------------------------------------------------------------------


class AdminAuth(AuthenticationBackend):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(secret_key=get_settings().secret_key)
        self.session_factory = session_factory

    async def _admin(self, user_id: Any) -> User | None:
        async with self.session_factory() as session:
            user = await session.get(User, user_id) if user_id else None
        if user is None or user.role != UserRole.ADMIN or not user.is_active:
            return None
        return user

    async def login(self, request: Request) -> bool:
        form = await request.form()
        async with self.session_factory() as session:
            user = await session.scalar(
                select(User).where(User.email == str(form.get("username", "")).lower())
            )
        if user is None or not verify_password(str(form.get("password", "")), user.password_hash):
            return False
        if await self._admin(user.id) is None:
            return False
        request.session["admin_id"] = user.id
        return True

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        # Роль и блокировка проверяются на каждом запросе.
        return await self._admin(request.session.get("admin_id")) is not None


# --- Базовый вид с журналом -----------------------------------------------------------


class AuditedView(ModelView):
    page_size = 50
    session_factory: async_sessionmaker[AsyncSession]

    async def _audit(self, request: Request, action: str, model: Any, fields: list[str]) -> None:
        pk = get_object_identifier(model)
        async with self.session_factory() as session:
            write_audit(
                session,
                request.session.get("admin_id"),
                action,
                model.__tablename__,
                int(pk) if str(pk).isdigit() else None,
                {"pk": str(pk), "fields": fields},
            )
            await session.commit()

    async def after_model_change(
        self, data: dict[str, Any], model: Any, is_created: bool, request: Request
    ) -> None:
        await self._audit(request, "create" if is_created else "update", model, sorted(data))

    async def after_model_delete(self, model: Any, request: Request) -> None:
        await self._audit(request, "delete", model, [])


def _link(url: str | None, text: str = "открыть") -> Markup | str:
    return Markup('<a href="{}" target="_blank">{}</a>').format(url, text) if url else "—"


def _redirect_back(request: Request) -> RedirectResponse:
    return RedirectResponse(request.headers.get("referer") or str(request.base_url), 302)


# --- Пользователи и KYC -------------------------------------------------------------


class UserAdmin(AuditedView, model=User):
    name, name_plural, icon = "Пользователь", "Пользователи", "fa-solid fa-user"
    can_create = False
    can_delete = False
    column_list = [
        User.id,
        User.email,
        User.full_name,
        User.is_verified,
        User.is_driver_license_verified,
        User.is_active,
        User.role,
    ]
    column_searchable_list = [User.email, User.full_name]
    column_details_exclude_list = [User.password_hash, User.checkin_pin]
    form_columns = [
        User.full_name,
        User.role,
        User.is_verified,
        User.is_driver_license_verified,
        User.is_active,
    ]
    column_labels = {
        User.is_verified: "Документ (ID) одобрен",
        User.is_driver_license_verified: "Права одобрены",
        User.is_active: "Активен (снять — заблокировать)",
        User.id_document_url: "Документ (ID)",
        User.driver_license_url: "Водительское удостоверение",
    }
    column_formatters_detail = {
        User.id_document_url: lambda m, a: _link(
            m.id_document_url and f"../../documents/{m.id}/id"
        ),
        User.driver_license_url: lambda m, a: _link(
            m.driver_license_url and f"../../documents/{m.id}/license"
        ),
    }

    async def on_model_change(
        self, data: dict[str, Any], model: Any, is_created: bool, request: Request
    ) -> None:
        # «На платформе с» — дата одобрения документов.
        if data.get("is_verified") and not model.is_verified:
            model.verified_at = rules.now_bishkek()
        elif "is_verified" in data and not data["is_verified"]:
            model.verified_at = None


class DocumentsView(BaseView):
    """Документы KYC из приватного хранилища — только для admin."""

    name = "Документы"

    def is_visible(self, request: Request) -> bool:
        return False

    @expose("/documents/{user_id}/{kind}", identity="documents")
    async def document(self, request: Request) -> Response:
        async with self.session_factory() as session:
            user = await session.get(User, int(request.path_params["user_id"]))
        name = None
        if user is not None:
            kind = request.path_params["kind"]
            name = user.id_document_url if kind == "id" else user.driver_license_url
        path = storage.private_path(name) if name else None
        if path is None:
            return Response("Документ не найден", status_code=404)
        return FileResponse(path)

    session_factory: async_sessionmaker[AsyncSession]


# --- Справочники ------------------------------------------------------------------


class RegionAdmin(AuditedView, model=Region):
    name, name_plural, icon = "Регион", "Регионы", "fa-solid fa-map"
    can_delete = False  # на регион ссылаются локации
    column_list = [Region.id, Region.name_ru, Region.slug, Region.is_active]


class LocalityAdmin(AuditedView, model=Locality):
    name, name_plural, icon = "Локация", "Локации", "fa-solid fa-location-dot"
    can_delete = False  # на локацию ссылаются хозяева, места и авто
    column_list = [
        Locality.id,
        Locality.name_ru,
        Locality.slug,
        Locality.region_id,
        Locality.is_active,
    ]
    form_columns = [
        Locality.region_id,
        Locality.name_ky,
        Locality.name_ru,
        Locality.name_en,
        Locality.slug,
        Locality.lat,
        Locality.lng,
        Locality.is_active,
    ]
    form_include_pk = True


class TagAdmin(AuditedView, model=Tag):
    name, name_plural, icon = "Тег", "Теги активностей", "fa-solid fa-tag"
    column_list = [Tag.id, Tag.name_ky, Tag.name_ru, Tag.name_en]


class PlaceAdmin(AuditedView, model=Place):
    name, name_plural, icon = "Место", "Места", "fa-solid fa-mountain"
    column_list = [Place.id, Place.name_ru, Place.locality_id]
    form_columns = [
        Place.locality_id,
        Place.name_ky,
        Place.name_ru,
        Place.name_en,
        Place.description_ky,
        Place.description_ru,
        Place.description_en,
    ]
    form_include_pk = True
    column_formatters_detail = {
        Place.photos: lambda m, a: Markup(" ").join(_link(p.url, f"фото {p.id}") for p in m.photos)
    }

    async def scaffold_form(self, rules: list[str] | None = None) -> Any:
        form = await super().scaffold_form(rules)
        form.new_photos = MultipleFileField("Добавить фото (JPEG / PNG / WebP)")
        return form

    async def on_model_change(
        self, data: dict[str, Any], model: Any, is_created: bool, request: Request
    ) -> None:
        for file in data.pop("new_photos", None) or []:
            if getattr(file, "filename", None):
                model.photos.append(PlacePhoto(url=await storage.save_photo(file)))


class PlacePhotoAdmin(AuditedView, model=PlacePhoto):
    name, name_plural, icon = "Фото места", "Фото мест", "fa-solid fa-image"
    can_create = can_edit = False
    column_list = [PlacePhoto.id, PlacePhoto.place_id, PlacePhoto.url]
    column_formatters = {PlacePhoto.url: lambda m, a: _link(m.url)}


class SettingAdmin(AuditedView, model=Setting):
    name, name_plural, icon = "Настройка", "Настройки", "fa-solid fa-gear"
    # Ключи читает код — новые не нужны, удаление сломает брони.
    can_create = can_delete = False
    column_list = [Setting.key, Setting.value]
    form_columns = [Setting.value]


# --- Хозяева, активности, авто -------------------------------------------------------


class HostProfileAdmin(AuditedView, model=HostProfile):
    name, name_plural, icon = "Хозяин", "Хозяева", "fa-solid fa-house"
    can_create = False
    column_list = [
        HostProfile.user_id,
        HostProfile.locality_id,
        HostProfile.capacity,
        HostProfile.price_per_person,
        HostProfile.is_published,
    ]
    # Переводы описаний, которые хозяин не заполнил, дописывает admin.
    form_columns = [
        HostProfile.description_ky,
        HostProfile.description_ru,
        HostProfile.description_en,
        HostProfile.is_published,
    ]


class HostPhotoAdmin(AuditedView, model=HostPhoto):
    name, name_plural, icon = "Фото хозяина", "Фото хозяев", "fa-solid fa-image"
    can_create = can_edit = False
    column_list = [HostPhoto.id, HostPhoto.host_id, HostPhoto.url]
    column_formatters = {HostPhoto.url: lambda m, a: _link(m.url)}


class ActivityAdmin(AuditedView, model=Activity):
    name, name_plural, icon = "Активность", "Активности", "fa-solid fa-person-hiking"
    can_create = can_delete = False
    column_list = [
        Activity.id,
        Activity.host_id,
        Activity.title_ru,
        Activity.price_per_person,
        Activity.is_active,
    ]
    form_columns = [
        Activity.title_ky,
        Activity.title_ru,
        Activity.title_en,
        Activity.description_ky,
        Activity.description_ru,
        Activity.description_en,
        Activity.tags,
        Activity.is_active,
    ]


class ActivityPhotoAdmin(AuditedView, model=ActivityPhoto):
    name, name_plural, icon = "Фото активности", "Фото активностей", "fa-solid fa-image"
    can_create = can_edit = False
    column_list = [ActivityPhoto.id, ActivityPhoto.activity_id, ActivityPhoto.url]
    column_formatters = {ActivityPhoto.url: lambda m, a: _link(m.url)}


class VehicleAdmin(AuditedView, model=Vehicle):
    name, name_plural, icon = "Авто", "Авто", "fa-solid fa-car"
    can_create = can_delete = False
    column_list = [
        Vehicle.id,
        Vehicle.owner_id,
        Vehicle.title,
        Vehicle.price_per_day,
        Vehicle.is_published,
    ]
    form_columns = [
        Vehicle.description_ky,
        Vehicle.description_ru,
        Vehicle.description_en,
        Vehicle.is_published,
    ]


class VehiclePhotoAdmin(AuditedView, model=VehiclePhoto):
    name, name_plural, icon = "Фото авто", "Фото авто", "fa-solid fa-image"
    can_create = can_edit = False
    column_list = [VehiclePhoto.id, VehiclePhoto.vehicle_id, VehiclePhoto.url]
    column_formatters = {VehiclePhoto.url: lambda m, a: _link(m.url)}


# --- Брони и платежи -------------------------------------------------------------


class NotAcknowledgedFilter:
    """Фильтр «хозяин не нажал «Вижу, жду гостя»»: оплаченные брони жилья без отметки."""

    has_operator = False
    template = "sqladmin/filters/lookup_filter.html"
    title = "«Вижу, жду гостя»"
    parameter_name = "not_acknowledged"

    async def lookups(self, request: Request, model: Any, run_query: Any) -> list[tuple[str, str]]:
        return [("all", "Все"), ("yes", "Хозяин не нажал — позвонить")]

    async def get_filtered_query(self, query: Any, value: Any, model: Any) -> Any:
        if value != "yes":
            return query
        return query.filter(
            Booking.status == BookingStatus.CONFIRMED,
            Booking.host_id.is_not(None),
            Booking.host_acknowledged_at.is_(None),
        )


class BookingAdmin(AuditedView, model=Booking):
    name, name_plural, icon = "Бронь", "Брони", "fa-solid fa-calendar-check"
    # Статусы меняются только через сервис (кнопки ниже), не через форму.
    can_create = can_edit = can_delete = False
    column_list = [
        Booking.id,
        Booking.type,
        Booking.status,
        Booking.guest_id,
        Booking.host_id,
        Booking.vehicle_id,
        Booking.start_date,
        Booking.end_date,
        Booking.host_acknowledged_at,
    ]
    column_default_sort = [(Booking.id, True)]
    column_labels = {Booking.host_acknowledged_at: "«Вижу, жду гостя»"}
    column_filters = [NotAcknowledgedFilter()]

    async def _cancel(self, request: Request, reason_valid: bool) -> Response:
        admin_id = request.session["admin_id"]
        for pk in filter(None, request.query_params.get("pks", "").split(",")):
            async with self.session_factory() as session:
                try:
                    await emergency_cancel(session, int(pk), admin_id, reason_valid)
                except AppError as exc:
                    return Response(f"Бронь #{pk}: {exc.message}", status_code=exc.status_code)
        return _redirect_back(request)

    @action(
        "cancel-valid",
        "Экстренная отмена: причина уважительная",
        "Гостю полный возврат. Продолжить?",
    )
    async def cancel_valid(self, request: Request) -> Response:
        return await self._cancel(request, reason_valid=True)

    @action(
        "cancel-invalid",
        "Экстренная отмена: причина неуважительная",
        "Гостю полный возврат, хозяину скрытая оценка 1, даты закроются в его календаре. "
        "Продолжить?",
    )
    async def cancel_invalid(self, request: Request) -> Response:
        return await self._cancel(request, reason_valid=False)


class PaymentAdmin(AuditedView, model=Payment):
    name, name_plural, icon = "Платёж", "Платежи", "fa-solid fa-money-bill"
    can_create = can_edit = can_delete = False
    column_list = [
        Payment.id,
        Payment.booking_id,
        Payment.pg_order_id,
        Payment.amount,
        Payment.refunded_amount,
        Payment.commission_amount,
        Payment.status,
        Payment.paid_at,
    ]
    column_default_sort = [(Payment.id, True)]


# --- Отзывы, чат, жалобы, поддержка ---------------------------------------------------


class ReviewAdmin(AuditedView, model=Review):
    name, name_plural, icon = "Отзыв", "Отзывы", "fa-solid fa-star"
    can_create = False
    column_list = [
        Review.id,
        Review.author_id,
        Review.host_id,
        Review.vehicle_id,
        Review.rating,
        Review.text_ru,
        Review.created_at,
    ]
    # Гость пишет на одном языке — переводы дописывает admin.
    form_columns = [Review.text_ky, Review.text_ru, Review.text_en]


class MessageAdmin(AuditedView, model=Message):
    name, name_plural, icon = "Сообщение", "Сообщения чата", "fa-solid fa-comments"
    can_create = can_edit = can_delete = False
    column_list = [Message.id, Message.conversation_id, Message.sender_id, Message.text]


class ComplaintAdmin(AuditedView, model=Complaint):
    name, name_plural, icon = "Жалоба", "Жалобы", "fa-solid fa-flag"
    can_create = can_delete = False
    column_list = [
        Complaint.id,
        Complaint.reporter_id,
        Complaint.target_user_id,
        Complaint.message_id,
        Complaint.text,
        Complaint.is_resolved,
    ]
    form_columns = [Complaint.is_resolved]


class SupportTicketAdmin(AuditedView, model=SupportTicket):
    name, name_plural, icon = "Обращение", "Обращения", "fa-solid fa-life-ring"
    can_create = can_delete = False
    column_list = [
        SupportTicket.id,
        SupportTicket.author_id,
        SupportTicket.booking_id,
        SupportTicket.description,
        SupportTicket.status,
        SupportTicket.created_at,
    ]
    column_default_sort = [(SupportTicket.id, True)]
    form_columns = [SupportTicket.status]


class SupportMessageAdmin(AuditedView, model=SupportMessage):
    """Ответ admin в обращение: выберите обращение и напишите текст."""

    name, name_plural, icon = "Ответ в обращение", "Ответы в обращения", "fa-solid fa-reply"
    can_edit = can_delete = False
    column_list = [
        SupportMessage.id,
        SupportMessage.ticket_id,
        SupportMessage.author_id,
        SupportMessage.text,
        SupportMessage.created_at,
    ]
    form_columns = [SupportMessage.ticket_id, SupportMessage.text]
    form_include_pk = True

    async def on_model_change(
        self, data: dict[str, Any], model: Any, is_created: bool, request: Request
    ) -> None:
        model.author_id = request.session["admin_id"]

    async def after_model_change(
        self, data: dict[str, Any], model: Any, is_created: bool, request: Request
    ) -> None:
        await super().after_model_change(data, model, is_created, request)
        async with self.session_factory() as session:
            ticket = await session.get(SupportTicket, model.ticket_id)
            assert ticket is not None
            notify(session, ticket.author_id, "support_reply", {"ticket_id": ticket.id})
            await session.commit()


class AuditLogAdmin(ModelView, model=AuditLog):
    name, name_plural, icon = "Запись журнала", "Журнал действий", "fa-solid fa-list"
    can_create = can_edit = can_delete = False
    column_list = [
        AuditLog.id,
        AuditLog.admin_id,
        AuditLog.action,
        AuditLog.entity_type,
        AuditLog.entity_id,
        AuditLog.created_at,
    ]
    column_default_sort = [(AuditLog.id, True)]


VIEWS: list[type[ModelView]] = [
    UserAdmin,
    BookingAdmin,
    PaymentAdmin,
    SupportTicketAdmin,
    SupportMessageAdmin,
    ComplaintAdmin,
    HostProfileAdmin,
    HostPhotoAdmin,
    ActivityAdmin,
    ActivityPhotoAdmin,
    VehicleAdmin,
    VehiclePhotoAdmin,
    ReviewAdmin,
    MessageAdmin,
    PlaceAdmin,
    PlacePhotoAdmin,
    TagAdmin,
    RegionAdmin,
    LocalityAdmin,
    SettingAdmin,
    AuditLogAdmin,
]


def mount_admin(app: FastAPI, session_factory: async_sessionmaker[AsyncSession]) -> None:
    admin = Admin(
        app,
        session_maker=session_factory,
        base_url="/admin",
        title="Конок — админка",
        authentication_backend=AdminAuth(session_factory),
    )
    for view in [*VIEWS, DocumentsView]:
        view.session_factory = session_factory
        admin.add_view(view)
