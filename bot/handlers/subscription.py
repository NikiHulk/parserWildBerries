from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from ..keyboards.inline import subscription_keyboard
from ..services.subscription import SubscriptionService

router = Router()


@router.message(Command("subscribe"))
async def subscribe(message: Message) -> None:
    await message.answer(
        "Выберите подходящий тариф подписки:",
        reply_markup=subscription_keyboard(),
    )


@router.callback_query(F.data.startswith("subscribe:"))
async def handle_subscription_callback(
    callback: CallbackQuery, subscription: SubscriptionService
) -> None:
    plan_name = callback.data.split(":", 1)[1]
    try:
        expires_at = subscription.activate(callback.from_user.id, plan_name)
    except ValueError:
        await callback.answer("Неизвестный тариф", show_alert=True)
        return

    await callback.answer("Подписка активирована!", show_alert=True)
    await callback.message.edit_text(
        f"Тариф {plan_name} активирован. Подписка действует до {expires_at:%d.%m.%Y %H:%M UTC}."
    )


@router.message(Command("status"))
async def status(message: Message, subscription: SubscriptionService) -> None:
    summary = subscription.get_subscription_summary(message.from_user.id)
    await message.answer(summary)


@router.message(Command("activate"))
async def activate_from_command(message: Message, subscription: SubscriptionService) -> None:
    parts = message.text.split()
    if len(parts) not in (2, 3):
        await message.answer(
            "Использование: /activate <plan> [duration_days]. Например: /activate monthly"
        )
        return

    _, plan_name, *maybe_duration = parts
    duration = int(maybe_duration[0]) if maybe_duration else None

    try:
        expires_at = subscription.activate(message.from_user.id, plan_name, duration)
    except ValueError as error:
        await message.answer(str(error))
        return

    await message.answer(
        f"Тариф {plan_name} активирован до {expires_at:%d.%m.%Y %H:%M UTC}."
    )
