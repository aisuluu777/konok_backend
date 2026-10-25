"""Все модели — импорт здесь нужен Alembic (autogenerate) и SQLAlchemy (связи по имени)."""

from app.models.activities import Activity, ActivityPhoto, Tag, activity_tags
from app.models.audit import AuditLog
from app.models.bookings import Booking, BookingActivity, BookingStatus, BookingType
from app.models.chat import Complaint, Conversation, Message
from app.models.favorites import Favorite
from app.models.geo import Locality, Region
from app.models.hosts import HostBlockedDate, HostPhoto, HostProfile
from app.models.notifications import Notification, PushSubscription
from app.models.payments import Payment, PaymentStatus
from app.models.places import Place, PlacePhoto
from app.models.reviews import Review
from app.models.settings import Setting
from app.models.support import SupportMessage, SupportTicket, TicketStatus
from app.models.users import User, UserRole
from app.models.vehicles import Vehicle, VehicleBlockedDate, VehiclePhoto

__all__ = [
    "Activity",
    "ActivityPhoto",
    "AuditLog",
    "Booking",
    "BookingActivity",
    "BookingStatus",
    "BookingType",
    "Complaint",
    "Conversation",
    "Favorite",
    "HostBlockedDate",
    "HostPhoto",
    "HostProfile",
    "Locality",
    "Message",
    "Notification",
    "Payment",
    "PaymentStatus",
    "Place",
    "PlacePhoto",
    "PushSubscription",
    "Region",
    "Review",
    "Setting",
    "SupportMessage",
    "SupportTicket",
    "TicketStatus",
    "Tag",
    "User",
    "UserRole",
    "Vehicle",
    "VehicleBlockedDate",
    "VehiclePhoto",
    "activity_tags",
]
