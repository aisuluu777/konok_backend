"""Статус брони failed, очередь web push (notifications.pushed_at), срок оплаты брони.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = (
    "'pending_payment', 'confirmed', 'in_progress', 'completed', "
    "'cancelled_by_guest', 'cancelled_by_admin'"
)
_NEW = _OLD + ", 'failed'"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_bookings_bookingstatus"), "bookings", type_="check")
    op.create_check_constraint(op.f("ck_bookings_bookingstatus"), "bookings", f"status IN ({_NEW})")
    op.add_column(
        "notifications", sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute("UPDATE notifications SET pushed_at = now()")  # старые уже отправлены
    op.execute("INSERT INTO settings (key, value) VALUES ('payment_minutes', '30')")


def downgrade() -> None:
    op.execute("DELETE FROM settings WHERE key = 'payment_minutes'")
    op.drop_column("notifications", "pushed_at")
    op.execute("UPDATE bookings SET status = 'cancelled_by_admin' WHERE status = 'failed'")
    op.drop_constraint(op.f("ck_bookings_bookingstatus"), "bookings", type_="check")
    op.create_check_constraint(op.f("ck_bookings_bookingstatus"), "bookings", f"status IN ({_OLD})")
