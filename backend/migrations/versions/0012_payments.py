"""payouts, payments, booking_activities

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQL — из docs/schema.sql (порядок: booking_activities → payouts → payments → FK).
    op.execute(
        "CREATE TYPE payment_status AS ENUM "
        "('pending', 'succeeded', 'failed', 'refunded', 'partially_refunded')"
    )
    op.execute("CREATE TYPE payout_status AS ENUM ('pending', 'paid', 'failed')")
    op.execute(
        """
        CREATE TABLE booking_activities (
          id                BIGSERIAL PRIMARY KEY,
          booking_id        BIGINT NOT NULL REFERENCES bookings(id),
          activity_id       BIGINT NOT NULL REFERENCES activities(id),
          activity_name     TEXT NOT NULL,
          persons           SMALLINT NOT NULL CHECK (persons > 0),
          price_per_person  NUMERIC(12,2) NOT NULL CHECK (price_per_person > 0),
          amount            NUMERIC(12,2) NOT NULL,
          payment_id        BIGINT NOT NULL,
          created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT booking_activities_amount_chk CHECK (amount = price_per_person * persons)
        )
        """
    )
    op.execute("CREATE INDEX booking_activities_booking_idx ON booking_activities (booking_id)")
    op.execute("CREATE INDEX booking_activities_payment_idx ON booking_activities (payment_id)")
    op.execute(
        """
        CREATE TABLE payouts (
          id                  BIGSERIAL PRIMARY KEY,
          booking_id          BIGINT NOT NULL REFERENCES bookings(id),
          recipient_id        BIGINT NOT NULL REFERENCES users(id),
          amount              NUMERIC(12,2) NOT NULL CHECK (amount > 0),
          currency            CHAR(3) NOT NULL DEFAULT 'KGS',
          status              payout_status NOT NULL DEFAULT 'pending',
          provider_reference  TEXT,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          paid_at             TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX payouts_booking_idx ON payouts (booking_id)")
    op.execute(
        """
        CREATE TABLE payments (
          id                       BIGSERIAL PRIMARY KEY,
          booking_id               BIGINT NOT NULL REFERENCES bookings(id),
          payer_id                 BIGINT NOT NULL REFERENCES users(id),
          provider                 TEXT NOT NULL DEFAULT 'mock',
          provider_transaction_id  TEXT,
          idempotency_key          TEXT NOT NULL UNIQUE,
          amount                   NUMERIC(12,2) NOT NULL CHECK (amount > 0),
          commission_amount        NUMERIC(12,2) NOT NULL CHECK (commission_amount >= 0),
          currency                 CHAR(3) NOT NULL DEFAULT 'KGS',
          status                   payment_status NOT NULL DEFAULT 'pending',
          payout_id                BIGINT REFERENCES payouts(id),
          paid_at                  TIMESTAMPTZ,
          created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT payments_commission_chk CHECK (commission_amount < amount)
        )
        """
    )
    op.execute("CREATE INDEX payments_booking_idx ON payments (booking_id)")
    op.execute(
        "CREATE INDEX payments_unpaid_out_idx ON payments (booking_id) "
        "WHERE status = 'succeeded' AND payout_id IS NULL"
    )
    op.execute(
        "ALTER TABLE booking_activities ADD CONSTRAINT booking_activities_payment_fk "
        "FOREIGN KEY (payment_id) REFERENCES payments(id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE booking_activities")
    op.execute("DROP TABLE payments")
    op.execute("DROP TABLE payouts")
    op.execute("DROP TYPE payout_status")
    op.execute("DROP TYPE payment_status")
