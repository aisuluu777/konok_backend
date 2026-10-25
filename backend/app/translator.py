"""Перевод сообщений чата через Azure AI Translator."""

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

LANGUAGES = ("ky", "ru", "en")


async def translate(text: str) -> dict[str, str] | None:
    """Перевод на ky / ru / en. None — ключа нет или Azure ответил ошибкой."""
    settings = get_settings()
    if not settings.azure_translator_key:
        return None
    headers = {"Ocp-Apim-Subscription-Key": settings.azure_translator_key}
    if settings.azure_translator_region:
        headers["Ocp-Apim-Subscription-Region"] = settings.azure_translator_region
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(
                f"{settings.azure_translator_endpoint}/translate",
                params=[("api-version", "3.0"), *[("to", lang) for lang in LANGUAGES]],
                headers=headers,
                json=[{"Text": text}],
            )
            response.raise_for_status()
            translations = response.json()[0]["translations"]
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        logger.exception("Ошибка перевода Azure")
        return None
    return {item["to"]: item["text"] for item in translations}
