from __future__ import annotations

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from ..services.subscription import SubscriptionService


class SubscriptionMiddleware(BaseMiddleware):
    def __init__(self, service: SubscriptionService) -> None:
        super().__init__()
        self._service = service

    async def __call__(self, handler, event: TelegramObject, data: dict):  # type: ignore[override]
        data.setdefault("subscription", self._service)
        return await handler(event, data)
