"""reviews

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQL — из docs/schema.sql.
    op.execute("CREATE TYPE review_status AS ENUM ('published', 'hidden')")
    op.execute(
        """
        CREATE TABLE reviews (
          id              BIGSERIAL PRIMARY KEY,
          booking_id      BIGINT NOT NULL REFERENCES bookings(id),
          author_id       BIGINT NOT NULL REFERENCES users(id),
          target_user_id  BIGINT NOT NULL REFERENCES users(id),
          rating          SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
          text            TEXT,
          status          review_status NOT NULL DEFAULT 'published',
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT reviews_booking_author_uq UNIQUE (booking_id, author_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX reviews_target_idx ON reviews (target_user_id) WHERE status = 'published'"
    )


def downgrade() -> None:
    op.execute("DROP TABLE reviews")
    op.execute("DROP TYPE review_status")
