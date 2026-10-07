import logging

logger = logging.getLogger(__name__)


async def send_sms(phone: str, text: str) -> None:
    """Заглушка: реальной отправки SMS в MVP нет, только лог без содержимого."""
    logger.info("SMS to %s queued (stub), %d chars", phone, len(text))
