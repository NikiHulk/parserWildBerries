from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..services.subscription import DEFAULT_PLANS


def subscription_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=(
                    f"{plan.name.title()}: {plan.description}"
                    f" ({plan.price_rub}₽)"
                ),
                callback_data=f"subscribe:{name}",
            )
        ]
        for name, plan in DEFAULT_PLANS.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def payment_keyboard(confirmation_url: str, payment_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Перейти к оплате",
                    url=confirmation_url,
                )
            ],
            [
                InlineKeyboardButton(
                    text="Проверить оплату",
                    callback_data=f"payment:check:{payment_id}",
                )
            ],
        ]
    )
