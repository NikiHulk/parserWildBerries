from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from ..keyboards import (
    BANNED_WORDS_BUTTON,
    SEARCH_BUTTON,
    main_menu_keyboard,
)

router = Router()

WELCOME_TEXT = (
    "Привет! Я помогу найти товары на Wildberries по заданным фильтрам.\n\n"
    "Что я умею:\n"
    f"• {SEARCH_BUTTON} — запусти подбор и получи карточки товаров с ценами, рейтингом, остатками и данными продавца.\n"
    f"• {BANNED_WORDS_BUTTON} — управляй словами, которые нужно исключить из выдачи (например, ‘восстановленный’).\n\n"
    "Нажми нужную кнопку ниже, чтобы начать работу."
)


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    await message.answer(WELCOME_TEXT, reply_markup=main_menu_keyboard())


@router.message(F.text.casefold() == "помощь")
@router.message(F.text.casefold() == "инструкция")
async def handle_help(message: Message) -> None:
    await message.answer(WELCOME_TEXT, reply_markup=main_menu_keyboard())
