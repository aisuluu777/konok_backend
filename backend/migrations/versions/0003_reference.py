"""reference data: regions, tags, places (+ seeds)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQL и сиды — из docs/schema.sql (разделы 2, 5, 12).
    op.execute(
        """
        CREATE TABLE regions (
          id    SMALLSERIAL PRIMARY KEY,
          name  TEXT NOT NULL UNIQUE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE tags (
          id    SMALLSERIAL PRIMARY KEY,
          name  TEXT NOT NULL UNIQUE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE places (
          id                 BIGSERIAL PRIMARY KEY,
          slug               TEXT NOT NULL UNIQUE,
          name               TEXT NOT NULL,
          region_id          SMALLINT NOT NULL REFERENCES regions(id),
          short_description  TEXT,
          description        TEXT,
          cover_url          TEXT,
          stops              JSONB NOT NULL DEFAULT '[]',
          travel_options     JSONB NOT NULL DEFAULT '[]',
          created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX places_region_idx ON places (region_id)")

    op.execute(
        """
        INSERT INTO regions (name) VALUES
          ('г. Бишкек'), ('г. Ош'),
          ('Чуйская область'), ('Иссык-Кульская область'), ('Нарынская область'),
          ('Таласская область'), ('Джалал-Абадская область'), ('Ошская область'),
          ('Баткенская область')
        """
    )
    op.execute(
        """
        INSERT INTO tags (name) VALUES
          ('Верховая езда'), ('Готовка'), ('Ремёсла'), ('Жизнь в юрте'), ('Походы'),
          ('Рыбалка'), ('Культура'), ('Фототуры'), ('Природа')
        """
    )
    op.execute(
        """
        INSERT INTO settings (key, value) VALUES
          ('hold_minutes', '15'),
          ('host_ack_reminder_hours', '24'),
          ('checkin_time', '13:00'),
          ('checkout_time', '11:00'),
          ('commission_rate', '0.1000')
        """
    )
    # Стартовые места (slug — из примеров schema.sql); описание админ заполнит сам (R-37).
    op.execute(
        """
        INSERT INTO places (slug, name, region_id)
        SELECT 'tash-rabat', 'Таш-Рабат', id FROM regions WHERE name = 'Нарынская область'
        UNION ALL
        SELECT 'issyk-kul', 'Иссык-Куль', id FROM regions WHERE name = 'Иссык-Кульская область'
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM settings WHERE key IN "
        "('hold_minutes', 'host_ack_reminder_hours', 'checkin_time', 'checkout_time', "
        "'commission_rate')"
    )
    op.execute("DROP TABLE places")
    op.execute("DROP TABLE tags")
    op.execute("DROP TABLE regions")
