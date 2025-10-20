from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from .config import get_settings
from .middlewares.subscription import SubscriptionMiddleware
from .services.banned_words import BannedWordsService
from .storage.database import Database
from .telegram.flow_search import router as ui_router

logging.basicConfig(level=logging.INFO)


def _build_dispatcher(banned_words: BannedWordsService) -> Dispatcher:
    dp = Dispatcher()
    dp.update.outer_middleware(
        SubscriptionMiddleware(
            banned_words=banned_words,
            # Для возврата платной модели передайте service=SubscriptionService(...) и payments=PaymentService(...)
        )
    )
    dp.include_router(ui_router)
    return dp


async def main() -> None:
    settings = get_settings()
    logging.getLogger(__name__).info(
        "WB detail config: max_batch=%s split_on_empty=%s",
        settings.wb_detail_max_batch,
        settings.wb_detail_split_on_empty,
    )
    logging.getLogger(__name__).info(
        "TG search config: timeout=%s max_pages=%s progress_interval=%s",
        settings.tg_search_timeout,
        settings.tg_search_max_pages,
        settings.tg_progress_interval,
    )
    logging.getLogger(__name__).info(
        "HTML fallback limit: %sms",
        settings.html_max_page_ms,
    )
    bot = Bot(settings.telegram_token)
    database = Database(settings.database_url)
    database.migrate()
    banned_words_service = BannedWordsService(database)

    dispatcher = _build_dispatcher(
        banned_words_service,
    )
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped")
