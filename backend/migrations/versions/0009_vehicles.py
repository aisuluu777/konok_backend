"""vehicles

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQL — из docs/schema.sql (CHECK и частичный индекс — как там).
    op.execute(
        """
        CREATE TABLE vehicles (
          id             BIGSERIAL PRIMARY KEY,
          owner_id       BIGINT NOT NULL REFERENCES users(id),
          region_id      SMALLINT NOT NULL REFERENCES regions(id),
          model          TEXT NOT NULL,
          year           SMALLINT,
          seats          SMALLINT,
          transmission   TEXT,
          fuel           TEXT,
          specs          JSONB NOT NULL DEFAULT '{}',
          included       JSONB NOT NULL DEFAULT '[]',
          photos         TEXT[] NOT NULL DEFAULT '{}',
          price_per_day  NUMERIC(12,2) NOT NULL CHECK (price_per_day > 0),
          with_driver    BOOLEAN NOT NULL DEFAULT false,
          is_published   BOOLEAN NOT NULL DEFAULT false,
          rating_avg     NUMERIC(3,2) NOT NULL DEFAULT 0,
          reviews_count  INTEGER NOT NULL DEFAULT 0,
          created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX vehicles_catalog_idx ON vehicles (region_id) WHERE is_published")


def downgrade() -> None:
    op.execute("DROP TABLE vehicles")
