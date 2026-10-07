"""foundation: btree_gist extension

Revision ID: 0001
Revises:
Create Date: 2026-10-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Нужен для EXCLUDE-ограничений против пересекающихся броней (schema.sql).
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
