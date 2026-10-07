"""Импорт всех моделей — чтобы Base.metadata знал все таблицы (Alembic, очистка в тестах)."""

from app.core.audit import AuditLog
from app.modules.activities.models import Activity
from app.modules.admin.models import Place, Region, Setting, Tag
from app.modules.bookings.models import Booking
from app.modules.chat.models import Conversation, Message
from app.modules.favorites.models import Favorite
from app.modules.guests.models import GuestProfile
from app.modules.hosts.models import HostProfile
from app.modules.kyc.models import KycVerification
from app.modules.notifications.models import Notification
from app.modules.payments.models import BookingActivity, Payment, Payout, Refund
from app.modules.reviews.models import Review
from app.modules.support.models import SupportTicket
from app.modules.users.models import User
from app.modules.vehicles.models import Vehicle

__all__ = [
    "Activity",
    "AuditLog",
    "Booking",
    "BookingActivity",
    "Conversation",
    "Favorite",
    "GuestProfile",
    "HostProfile",
    "KycVerification",
    "Message",
    "Notification",
    "Payment",
    "Payout",
    "Place",
    "Refund",
    "Region",
    "Review",
    "Setting",
    "SupportTicket",
    "Tag",
    "User",
    "Vehicle",
]
