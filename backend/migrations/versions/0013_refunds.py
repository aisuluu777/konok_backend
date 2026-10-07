"""refunds

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQL — из docs/schema.sql.
    op.execute("CREATE TYPE refund_status AS ENUM ('pending', 'succeeded', 'failed')")
    op.execute(
        """
        CREATE TABLE refunds (
          id            BIGSERIAL PRIMARY KEY,
          payment_id    BIGINT NOT NULL REFERENCES payments(id),
          amount        NUMERIC(12,2) NOT NULL CHECK (amount > 0),
          reason        TEXT,
          status        refund_status NOT NULL DEFAULT 'pending',
          initiated_by  BIGINT REFERENCES users(id),
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          processed_at  TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX refunds_payment_idx ON refunds (payment_id)")


def downgrade() -> None:
    op.execute("DROP TABLE refunds")
    op.execute("DROP TYPE refund_status")
