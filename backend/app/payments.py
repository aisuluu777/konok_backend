"""Платёжный провайдер. Сейчас одна реализация — MockPaymentProvider (без реальных денег)."""

from decimal import Decimal
from typing import Protocol

from app.config import get_settings


class PaymentProvider(Protocol):
    async def create_payment(self, order_id: str, amount: Decimal) -> str | None:
        """Создаёт платёж у провайдера; возвращает URL платёжной страницы."""
        ...

    async def refund(self, order_id: str, amount: Decimal) -> None:
        """Возвращает гостю amount по платежу order_id."""
        ...


class MockPaymentProvider:
    """Платёжной страницы нет: платёж оплачивается вызовом POST /payments/{id}/mock-pay."""

    async def create_payment(self, order_id: str, amount: Decimal) -> str | None:
        return None

    async def refund(self, order_id: str, amount: Decimal) -> None:
        return None


def get_payment_provider() -> PaymentProvider:
    provider = get_settings().payment_provider
    if provider == "mock":
        return MockPaymentProvider()
    raise RuntimeError(f"Неизвестный PAYMENT_PROVIDER: {provider}")
