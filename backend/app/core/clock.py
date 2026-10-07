from datetime import date, datetime
from zoneinfo import ZoneInfo

# «Сегодня» для бизнес-правил — по Бишкеку (UTC+6); в БД время хранится в TIMESTAMPTZ.
BISHKEK = ZoneInfo("Asia/Bishkek")


def today_bishkek() -> date:
    return datetime.now(BISHKEK).date()
