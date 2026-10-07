"""kyc_verifications (+ settings.kyc_resubmit_hours)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQL — из docs/schema.sql.
    op.execute("CREATE TYPE resident_type AS ENUM ('kg_resident', 'foreigner')")
    op.execute(
        """
        CREATE TABLE kyc_verifications (
          id                  BIGSERIAL PRIMARY KEY,
          user_id             BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          resident_type       resident_type,
          provider            TEXT NOT NULL DEFAULT 'manual',
          provider_reference  TEXT,
          status              kyc_status NOT NULL DEFAULT 'pending',
          passport_file_url   TEXT,
          selfie_file_url     TEXT,
          rejection_reason    TEXT,
          reviewed_by         BIGINT REFERENCES users(id),
          submitted_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          checked_at          TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX kyc_user_idx ON kyc_verifications (user_id, submitted_at DESC)")
    op.execute("CREATE INDEX kyc_queue_idx ON kyc_verifications (status) WHERE status = 'pending'")
    # Пауза перед повторной подачей после отказа (решение пользователя 2026-10-05).
    op.execute("INSERT INTO settings (key, value) VALUES ('kyc_resubmit_hours', '1')")


def downgrade() -> None:
    op.execute("DELETE FROM settings WHERE key = 'kyc_resubmit_hours'")
    op.execute("DROP TABLE kyc_verifications")
    op.execute("DROP TYPE resident_type")
