"""activities, activity_tags

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQL — из docs/schema.sql (CHECK и частичный индекс — как там).
    op.execute(
        """
        CREATE TABLE activities (
          id                BIGSERIAL PRIMARY KEY,
          host_id           BIGINT NOT NULL REFERENCES host_profiles(user_id) ON DELETE CASCADE,
          name              TEXT NOT NULL,
          description       TEXT,
          price_per_person  NUMERIC(12,2) NOT NULL CHECK (price_per_person > 0),
          is_active         BOOLEAN NOT NULL DEFAULT true,
          created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX activities_host_idx ON activities (host_id) WHERE is_active")
    op.execute(
        """
        CREATE TABLE activity_tags (
          activity_id  BIGINT   NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
          tag_id       SMALLINT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
          PRIMARY KEY (activity_id, tag_id)
        )
        """
    )
    op.execute("CREATE INDEX activity_tags_tag_idx ON activity_tags (tag_id)")


def downgrade() -> None:
    op.execute("DROP TABLE activity_tags")
    op.execute("DROP TABLE activities")
