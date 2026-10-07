"""users.checkin_pin вместо bookings.checkin_pin_hash

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-06

R-23 изменено пользователем: PIN один на пользователя, выдаётся при одобрении KYC,
хранится открыто (решение MVP, не окончательное).
"""

import secrets
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN checkin_pin TEXT CHECK (checkin_pin ~ '^[0-9]{6}$')")
    # Уже одобренные пользователи тоже должны иметь PIN (secrets, как в коде).
    bind = op.get_bind()
    ids = bind.execute(sa.text("SELECT id FROM users WHERE kyc_status = 'approved'")).scalars()
    for user_id in ids.all():
        bind.execute(
            sa.text("UPDATE users SET checkin_pin = :pin WHERE id = :id"),
            {"pin": f"{secrets.randbelow(10**6):06d}", "id": user_id},
        )
    op.execute("ALTER TABLE bookings DROP COLUMN checkin_pin_hash")


def downgrade() -> None:
    op.execute("ALTER TABLE bookings ADD COLUMN checkin_pin_hash TEXT")
    op.execute("ALTER TABLE users DROP COLUMN checkin_pin")
