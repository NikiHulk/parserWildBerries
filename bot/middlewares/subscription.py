from __future__ import annotations

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from ..services.banned_words import BannedWordsService
from ..services.subscription import SubscriptionService


class SubscriptionMiddleware(BaseMiddleware):
    def __init__(
        self,
        service: SubscriptionService,
        banned_words: BannedWordsService | None = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._banned_words = banned_words

    async def __call__(self, handler, event: TelegramObject, data: dict):  # type: ignore[override]
        data.setdefault("subscription", self._service)
        if self._banned_words is not None:
            data.setdefault("banned_words", self._banned_words)
        return await handler(event, data)
