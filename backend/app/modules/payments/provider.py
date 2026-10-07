"""Платёжный провайдер (R-30). Сейчас единственная реализация — имитация без реальных денег."""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class ChargeResult:
    success: bool
    transaction_id: str | None = None


class PaymentProvider(Protocol):
    name: str

    async def charge(
        self, *, amount: Decimal, currency: str, idempotency_key: str
    ) -> ChargeResult: ...

    async def refund(self, *, transaction_id: str | None, amount: Decimal) -> bool: ...


class MockPaymentProvider:
    """Всегда успешен; для тестов можно настроить на отказ (R-31)."""

    name = "mock"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def charge(self, *, amount: Decimal, currency: str, idempotency_key: str) -> ChargeResult:
        if self.fail:
            return ChargeResult(success=False)
        return ChargeResult(success=True, transaction_id=f"mock-{uuid.uuid4().hex}")

    async def refund(self, *, transaction_id: str | None, amount: Decimal) -> bool:
        return True  # R-34: mock-возврат всегда успешен


_provider: PaymentProvider = MockPaymentProvider()


def get_payment_provider() -> PaymentProvider:
    """Зависимость FastAPI; реальный провайдер позже подменит только эту функцию (R-30)."""
    return _provider
