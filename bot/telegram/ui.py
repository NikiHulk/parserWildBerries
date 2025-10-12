"""Keyboards for the Telegram interface."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

SEARCH_BUTTON = "🔎 Поиск"
CANCEL_BUTTON = "❌ Отмена"


def main_kb() -> ReplyKeyboardMarkup:
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton(SEARCH_BUTTON))
    return keyboard


def cancel_kb() -> ReplyKeyboardMarkup:
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, selective=True)
    keyboard.add(KeyboardButton(CANCEL_BUTTON))
    return keyboard


def pager_kb(page: int, total_pages: int) -> InlineKeyboardMarkup:
    left_page = max(0, page - 1)
    right_page = min(total_pages - 1, page + 1)
    keyboard = InlineKeyboardMarkup()
    keyboard.add(
        InlineKeyboardButton("◀️", callback_data=f"pg:{left_page}"),
        InlineKeyboardButton("▶️", callback_data=f"pg:{right_page}"),
    )
    return keyboard
