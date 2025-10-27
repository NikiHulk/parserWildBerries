from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

SEARCH_BUTTON = "🔍 Поиск товара"
BANNED_WORDS_BUTTON = "🚫 Запрещённые слова"
BACK_TO_MENU_BUTTON = "⬅️ В главное меню"
CANCEL_BUTTON = "⬅️ Отмена"
ADD_BANNED_BUTTON = "➕ Добавить слова"
REMOVE_BANNED_BUTTON = "➖ Удалить слово"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=SEARCH_BUTTON)],
            [KeyboardButton(text=BANNED_WORDS_BUTTON)],
        ],
        resize_keyboard=True,
    )


def banned_words_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ADD_BANNED_BUTTON)],
            [KeyboardButton(text=REMOVE_BANNED_BUTTON)],
            [KeyboardButton(text=BACK_TO_MENU_BUTTON)],
        ],
        resize_keyboard=True,
    )


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=CANCEL_BUTTON)]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
