"""Keyboards for the Telegram interface."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

SEARCH_BUTTON = "🔎 Поиск"
SETTINGS_BUTTON = "⚙️ Настройки"
HELP_BUTTON = "ℹ️ Помощь"
CANCEL_BUTTON = "❌ Отмена"


def main_kb() -> ReplyKeyboardMarkup:
    """Main menu keyboard shown in idle state."""

    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=SEARCH_BUTTON)],
            [
                KeyboardButton(text=SETTINGS_BUTTON),
                KeyboardButton(text=HELP_BUTTON),
            ],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Выберите действие",
    )


def cancel_kb() -> ReplyKeyboardMarkup:
    """Standalone cancel keyboard (legacy scenarios)."""

    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=CANCEL_BUTTON)]],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Отправьте текст или отмените",
    )


def remove_kb() -> ReplyKeyboardRemove:
    """Hide reply keyboard to allow free text input."""

    return ReplyKeyboardRemove()


def pager_kb(page: int, total_pages: int) -> InlineKeyboardMarkup:
    left_page = max(0, page - 1)
    right_page = min(total_pages - 1, page + 1)
    keyboard = InlineKeyboardMarkup()
    keyboard.add(
        InlineKeyboardButton("◀️", callback_data=f"pg:{left_page}"),
        InlineKeyboardButton("▶️", callback_data=f"pg:{right_page}"),
    )
    return keyboard
