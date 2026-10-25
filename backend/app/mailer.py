import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.config import get_settings

logger = logging.getLogger(__name__)


def _send(to: str, subject: str, text: str) -> None:
    settings = get_settings()
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)


async def send_email(to: str, subject: str, text: str) -> None:
    if not get_settings().smtp_host:
        logger.warning("SMTP не настроен. Письмо для %s: %s — %s", to, subject, text)
        return
    await asyncio.to_thread(_send, to, subject, text)
