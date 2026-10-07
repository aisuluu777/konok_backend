"""bookings

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQL — из docs/schema.sql. EXCLUDE autogenerate не видит — только вручную (btree_gist — 0001).
    op.execute("CREATE TYPE booking_type AS ENUM ('stay', 'vehicle')")
    op.execute(
        """
        CREATE TYPE booking_status AS ENUM (
          'hold', 'confirmed', 'in_progress', 'completed',
          'cancelled_by_guest', 'cancelled_by_support', 'expired', 'no_show'
        )
        """
    )
    op.execute(
        """
        CREATE TABLE bookings (
          id                    BIGSERIAL PRIMARY KEY,
          type                  booking_type NOT NULL,
          guest_id              BIGINT NOT NULL REFERENCES users(id),
          host_id               BIGINT REFERENCES host_profiles(user_id),
          vehicle_id            BIGINT REFERENCES vehicles(id),
          start_date            DATE NOT NULL,
          end_date              DATE NOT NULL,
          unit_price            NUMERIC(12,2) NOT NULL CHECK (unit_price > 0),
          base_amount           NUMERIC(12,2) NOT NULL,
          commission_rate       NUMERIC(5,4) NOT NULL
                                CHECK (commission_rate >= 0 AND commission_rate < 1),
          currency              CHAR(3) NOT NULL DEFAULT 'KGS',
          status                booking_status NOT NULL DEFAULT 'hold',
          hold_expires_at       TIMESTAMPTZ NOT NULL,
          confirmed_at          TIMESTAMPTZ,
          completed_at          TIMESTAMPTZ,
          cancelled_at          TIMESTAMPTZ,
          cancelled_by          BIGINT REFERENCES users(id),
          cancel_reason         TEXT,

          guests_count          SMALLINT CHECK (guests_count > 0),
          arrival_time          TIME,
          host_acknowledged_at  TIMESTAMPTZ,
          checkin_pin_hash      TEXT,
          checkin_pin_attempts  SMALLINT NOT NULL DEFAULT 0,
          checked_in_at         TIMESTAMPTZ,

          pickup_location       TEXT,
          return_location       TEXT,

          created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

          CONSTRAINT bookings_host_chk     CHECK ((type = 'stay')    = (host_id IS NOT NULL)),
          CONSTRAINT bookings_guests_chk   CHECK ((type = 'stay')    = (guests_count IS NOT NULL)),
          CONSTRAINT bookings_arrival_chk  CHECK ((type = 'stay')    = (arrival_time IS NOT NULL)),
          CONSTRAINT bookings_vehicle_chk  CHECK ((type = 'vehicle') = (vehicle_id IS NOT NULL)),
          CONSTRAINT bookings_pickup_chk
            CHECK ((type = 'vehicle') = (pickup_location IS NOT NULL)),
          CONSTRAINT bookings_return_chk
            CHECK ((type = 'vehicle') = (return_location IS NOT NULL)),

          CONSTRAINT bookings_dates_chk    CHECK (end_date > start_date),
          CONSTRAINT bookings_not_self_chk CHECK (guest_id <> host_id),
          CONSTRAINT bookings_base_amount_chk
            CHECK (base_amount = unit_price * COALESCE(guests_count, 1) * (end_date - start_date)),

          CONSTRAINT bookings_guest_no_overlap EXCLUDE USING gist (
            guest_id WITH =,
            daterange(start_date, end_date, '[)') WITH &&
          ) WHERE (type = 'stay' AND status IN ('hold', 'confirmed', 'in_progress')),

          CONSTRAINT bookings_vehicle_no_overlap EXCLUDE USING gist (
            vehicle_id WITH =,
            daterange(start_date, end_date, '[)') WITH &&
          ) WHERE (type = 'vehicle' AND status IN ('hold', 'confirmed', 'in_progress'))
        )
        """
    )
    op.execute(
        "CREATE INDEX bookings_host_idx ON bookings (host_id, start_date) WHERE type = 'stay'"
    )
    op.execute(
        "CREATE INDEX bookings_vehicle_idx ON bookings (vehicle_id, start_date) "
        "WHERE type = 'vehicle'"
    )
    op.execute("CREATE INDEX bookings_guest_idx ON bookings (guest_id, start_date DESC)")
    op.execute("CREATE INDEX bookings_hold_idx ON bookings (hold_expires_at) WHERE status = 'hold'")
    op.execute(
        "CREATE INDEX bookings_ack_idx ON bookings (start_date) "
        "WHERE type = 'stay' AND status = 'confirmed' AND host_acknowledged_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE bookings")
    op.execute("DROP TYPE booking_status")
    op.execute("DROP TYPE booking_type")
