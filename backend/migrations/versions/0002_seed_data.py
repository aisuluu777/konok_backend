"""Стартовые данные: регион Иссык-Куль, локация Каракол, глобальные настройки.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO regions (name_ky, name_ru, name_en, slug, is_active)
        VALUES ('Ысык-Көл облусу', 'Иссык-Кульская область', 'Issyk-Kul Region', 'issyk-kul', true)
        """
    )
    op.execute(
        """
        INSERT INTO localities (region_id, name_ky, name_ru, name_en, slug, lat, lng, is_active)
        SELECT id, 'Каракол', 'Каракол', 'Karakol', 'karakol', 42.490700, 78.393600, true
        FROM regions WHERE slug = 'issyk-kul'
        """
    )
    op.execute(
        """
        INSERT INTO settings (key, value) VALUES
          ('commission_rate', '0.10'),
          ('checkin_time', '13:00'),
          ('checkout_time', '12:00')
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM settings WHERE key IN ('commission_rate', 'checkin_time', 'checkout_time')"
    )
    op.execute("DELETE FROM localities WHERE slug = 'karakol'")
    op.execute("DELETE FROM regions WHERE slug = 'issyk-kul'")
