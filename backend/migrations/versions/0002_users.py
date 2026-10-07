"""users, audit_logs, settings (+ TTL seeds)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQL — дословно из docs/schema.sql (типы, users, audit_logs, settings).
    op.execute("CREATE TYPE user_role AS ENUM ('user', 'moderator', 'admin')")
    op.execute("CREATE TYPE user_status AS ENUM ('active', 'blocked')")
    op.execute(
        "CREATE TYPE kyc_status AS ENUM "
        "('not_started', 'pending', 'approved', 'rejected', 'resubmission')"
    )

    # settings перенесена сюда из Step 2: auth читает из неё TTL (решение 2026-10-05).
    op.execute(
        """
        CREATE TABLE settings (
          key         TEXT PRIMARY KEY,
          value       TEXT NOT NULL,
          updated_by  BIGINT,
          updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE users (
          id                 BIGSERIAL PRIMARY KEY,
          role               user_role   NOT NULL DEFAULT 'user',
          status             user_status NOT NULL DEFAULT 'active',
          first_name         TEXT NOT NULL,
          last_name          TEXT NOT NULL,
          email              TEXT NOT NULL,
          phone              TEXT NOT NULL,
          password_hash      TEXT NOT NULL,
          phone_verified_at  TIMESTAMPTZ,
          terms_accepted_at  TIMESTAMPTZ NOT NULL,
          kyc_status         kyc_status NOT NULL DEFAULT 'not_started',
          languages          TEXT[] NOT NULL DEFAULT '{}',
          created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX users_email_uq ON users (lower(email))")
    op.execute("CREATE UNIQUE INDEX users_phone_uq ON users (phone)")
    op.execute(
        "ALTER TABLE settings ADD CONSTRAINT settings_updated_by_fk "
        "FOREIGN KEY (updated_by) REFERENCES users(id)"
    )

    op.execute(
        """
        CREATE TABLE audit_logs (
          id           BIGSERIAL PRIMARY KEY,
          actor_id     BIGINT REFERENCES users(id),
          action       TEXT NOT NULL,
          entity_type  TEXT NOT NULL,
          entity_id    BIGINT,
          details      JSONB NOT NULL DEFAULT '{}',
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX audit_logs_entity_idx ON audit_logs (entity_type, entity_id, created_at DESC)"
    )

    op.execute(
        """
        INSERT INTO settings (key, value) VALUES
          ('access_token_ttl_min', '15'),
          ('refresh_token_ttl_days', '30'),
          ('otp_ttl_sec', '300'),
          ('otp_resend_sec', '45')
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE audit_logs")
    op.execute("ALTER TABLE settings DROP CONSTRAINT settings_updated_by_fk")
    op.execute("DROP TABLE users")
    op.execute("DROP TABLE settings")
    op.execute("DROP TYPE kyc_status")
    op.execute("DROP TYPE user_status")
    op.execute("DROP TYPE user_role")
