"""favorites

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQL — из docs/schema.sql.
    op.execute(
        """
        CREATE TABLE favorites (
          id          BIGSERIAL PRIMARY KEY,
          user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          host_id     BIGINT REFERENCES host_profiles(user_id) ON DELETE CASCADE,
          place_id    BIGINT REFERENCES places(id) ON DELETE CASCADE,
          vehicle_id  BIGINT REFERENCES vehicles(id) ON DELETE CASCADE,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT favorites_one_target_chk
            CHECK (num_nonnulls(host_id, place_id, vehicle_id) = 1)
        )
        """
    )
    for column in ("host_id", "place_id", "vehicle_id"):
        name = column.removesuffix("_id")
        op.execute(
            f"CREATE UNIQUE INDEX favorites_{name}_uq ON favorites (user_id, {column}) "
            f"WHERE {column} IS NOT NULL"
        )


def downgrade() -> None:
    op.execute("DROP TABLE favorites")
