from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode

from .config import get_settings
from .handlers import search, subscription
from .middlewares.subscription import SubscriptionMiddleware
from .services.subscription import SubscriptionService
from .storage.database import Database

logging.basicConfig(level=logging.INFO)


def _build_dispatcher(subscription_service: SubscriptionService) -> Dispatcher:
    dp = Dispatcher()
    dp.update.outer_middleware(SubscriptionMiddleware(subscription_service))
    dp.include_router(subscription.router)
    dp.include_router(search.router)
    return dp


async def main() -> None:
    settings = get_settings()
    bot = Bot(settings.telegram_token, parse_mode=ParseMode.HTML)
    database = Database(settings.database_url)
    subscription_service = SubscriptionService(database)

    dispatcher = _build_dispatcher(subscription_service)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped")
