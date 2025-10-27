from __future__ import annotations

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from ..services.banned_words import BannedWordsService
from ..services.payments import PaymentService
from ..services.subscription import SubscriptionService


class SubscriptionMiddleware(BaseMiddleware):
    """Inject shared services into handler data."""

    def __init__(
        self,
        service: SubscriptionService | None = None,
        banned_words: BannedWordsService | None = None,
        payments: PaymentService | None = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._banned_words = banned_words
        self._payments = payments

    async def __call__(self, handler, event: TelegramObject, data: dict):  # type: ignore[override]
        if self._service is not None:
            data.setdefault("subscription", self._service)
        if self._banned_words is not None:
            data.setdefault("banned_words", self._banned_words)
        if self._payments is not None:
            data.setdefault("payments", self._payments)
        return await handler(event, data)
