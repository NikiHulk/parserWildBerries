from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..services.subscription import DEFAULT_PLANS


def subscription_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=f"{plan.name.title()}: {plan.description}",
                callback_data=f"subscribe:{name}",
            )
        ]
        for name, plan in DEFAULT_PLANS.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
