"""Keyboards for the Telegram interface."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from ..config import get_settings

_settings = get_settings()

SEARCH_BUTTON = "🔎 Поиск"
SETTINGS_BUTTON = "⚙️ Настройки"
HELP_BUTTON = "ℹ️ Помощь"
CANCEL_BUTTON = _settings.tg_cancel_text


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


def cancel_kb(text: str) -> ReplyKeyboardMarkup:
    """Single cancel button keyboard used during input steps."""

    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=text)]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def cancel_skip_kb(cancel_text: str, skip_text: str) -> ReplyKeyboardMarkup:
    """Cancel/skip keyboard for optional steps."""

    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=cancel_text), KeyboardButton(text=skip_text)]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def remove_kb() -> ReplyKeyboardRemove:
    """Hide reply keyboard to allow free text input."""

    return ReplyKeyboardRemove()


def pager_kb(page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Inline keyboard for navigating result pages."""

    left_page = max(0, page - 1)
    right_page = min(total_pages - 1, page + 1)
    keyboard = InlineKeyboardMarkup()
    keyboard.add(
        InlineKeyboardButton("◀️", callback_data=f"pg:{left_page}"),
        InlineKeyboardButton("▶️", callback_data=f"pg:{right_page}"),
    )
    return keyboard
