"""guest_profiles

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQL — из docs/schema.sql.
    op.execute(
        """
        CREATE TABLE guest_profiles (
          user_id     BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          country     TEXT,
          birth_date  DATE,
          avatar_url  TEXT,
          interests   TEXT[] NOT NULL DEFAULT '{}',
          updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE guest_profiles")
