"""support_tickets

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQL — из docs/schema.sql.
    op.execute(
        "CREATE TYPE ticket_status AS ENUM "
        "('new', 'in_progress', 'waiting_reply', 'resolved', 'closed')"
    )
    op.execute(
        """
        CREATE TABLE support_tickets (
          id           BIGSERIAL PRIMARY KEY,
          user_id      BIGINT REFERENCES users(id),
          booking_id   BIGINT REFERENCES bookings(id),
          category     TEXT NOT NULL,
          description  TEXT,
          status       ticket_status NOT NULL DEFAULT 'new',
          assigned_to  BIGINT REFERENCES users(id),
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX support_tickets_queue_idx ON support_tickets (status, created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE support_tickets")
    op.execute("DROP TYPE ticket_status")
