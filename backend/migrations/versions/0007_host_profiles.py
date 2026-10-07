"""host_profiles

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQL — из docs/schema.sql (CHECK и частичный индекс — как там).
    op.execute(
        """
        CREATE TABLE host_profiles (
          user_id                 BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          region_id               SMALLINT NOT NULL REFERENCES regions(id),
          settlement              TEXT NOT NULL,
          address                 TEXT NOT NULL,
          latitude                NUMERIC(9,6),
          longitude               NUMERIC(9,6),
          about                   TEXT,
          avatar_url              TEXT,
          photos                  TEXT[] NOT NULL DEFAULT '{}',
          video_url               TEXT,
          video_duration_sec      SMALLINT CHECK (video_duration_sec BETWEEN 1 AND 120),
          capacity                SMALLINT NOT NULL CHECK (capacity > 0),
          price_per_person_night  NUMERIC(12,2) NOT NULL CHECK (price_per_person_night > 0),
          blocked_dates           DATE[] NOT NULL DEFAULT '{}',
          is_guide                BOOLEAN NOT NULL DEFAULT false,
          is_published            BOOLEAN NOT NULL DEFAULT false,
          rating_avg              NUMERIC(3,2) NOT NULL DEFAULT 0,
          reviews_count           INTEGER NOT NULL DEFAULT 0,
          created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX host_profiles_catalog_idx ON host_profiles (region_id) WHERE is_published"
    )


def downgrade() -> None:
    op.execute("DROP TABLE host_profiles")
