"""notifications

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQL — из docs/schema.sql.
    op.execute(
        """
        CREATE TABLE notifications (
          id          BIGSERIAL PRIMARY KEY,
          user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          type        TEXT NOT NULL,
          channel     TEXT NOT NULL DEFAULT 'in_app',
          payload     JSONB NOT NULL DEFAULT '{}',
          sent_at     TIMESTAMPTZ,
          read_at     TIMESTAMPTZ,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX notifications_user_idx ON notifications (user_id, created_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE notifications")
