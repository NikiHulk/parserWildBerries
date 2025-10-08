from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from ..keyboards.inline import payment_keyboard, subscription_keyboard
from ..services.payments import PaymentService
from ..services.subscription import SubscriptionPlan, SubscriptionService

router = Router()


@router.message(Command("subscribe"))
async def subscribe(message: Message) -> None:
    await message.answer(
        "Выберите подходящий тариф подписки:",
        reply_markup=subscription_keyboard(),
    )


@router.callback_query(F.data.startswith("subscribe:"))
async def handle_subscription_callback(
    callback: CallbackQuery,
    subscription: SubscriptionService,
    payments: PaymentService | None = None,
) -> None:
    plan_name = callback.data.split(":", 1)[1]
    try:
        plan: SubscriptionPlan = subscription.get_plan(plan_name)
    except ValueError:
        await callback.answer("Неизвестный тариф", show_alert=True)
        return

    if plan.price_rub == 0:
        expires_at = subscription.activate(callback.from_user.id, plan_name)
        await callback.answer("Подписка активирована!", show_alert=True)
        await callback.message.edit_text(
            f"Тариф {plan.name} активирован. Подписка действует до"
            f" {expires_at:%d.%m.%Y %H:%M UTC}."
        )
        return

    if payments is None or not payments.enabled:
        await callback.answer("Оплата временно недоступна", show_alert=True)
        await callback.message.answer(
            "Платёжный сервис не настроен. Свяжитесь с администратором."
        )
        return

    payment = await payments.create_payment(
        user_id=callback.from_user.id,
        plan_name=plan.name,
        amount_rub=plan.price_rub,
    )

    await callback.answer()
    await callback.message.edit_text(
        (
            f"Для активации тарифа {plan.name} оплатите {payment.amount:.0f}₽"
            " через YooKassa по ссылке ниже. После оплаты нажмите"
            " кнопку \"Проверить оплату\"."
        ),
        reply_markup=payment_keyboard(
            payment.confirmation_url,
            payment.payment_id,
        ),
    )


@router.callback_query(F.data.startswith("payment:check:"))
async def check_payment_status(
    callback: CallbackQuery,
    subscription: SubscriptionService,
    payments: PaymentService,
) -> None:
    if not payments.enabled:
        await callback.answer("Платёжный сервис не настроен", show_alert=True)
        return

    payment_id = callback.data.split(":", 2)[2]
    stored = payments.get_stored_payment(payment_id)
    if stored is None:
        await callback.answer("Платёж не найден", show_alert=True)
        return

    stored_user = int(stored["user_id"]) if stored.get("user_id") else None
    if stored_user and stored_user != callback.from_user.id:
        await callback.answer("Этот платёж принадлежит другому пользователю.", show_alert=True)
        return

    if stored.get("status") == "succeeded":
        await callback.answer("Оплата уже подтверждена", show_alert=True)
        return

    latest = await payments.refresh_payment(payment_id)
    if latest is None:
        await callback.answer("Не удалось обновить статус", show_alert=True)
        return

    status = latest["status"]
    if status == "succeeded":
        plan_name = stored.get("plan") or latest.get("plan") or ""
        if not plan_name:
            await callback.answer("Не удалось определить тариф", show_alert=True)
            return
        try:
            expires_at = subscription.activate(callback.from_user.id, plan_name)
        except ValueError:
            await callback.answer("Не удалось активировать подписку", show_alert=True)
            return

        await callback.answer("Оплата подтверждена!", show_alert=True)
        await callback.message.edit_text(
            f"Оплата получена. Тариф {plan_name} активен до"
            f" {expires_at:%d.%m.%Y %H:%M UTC}."
        )
    elif status in {"waiting_for_capture", "pending"}:
        await callback.answer("Платёж ещё обрабатывается", show_alert=True)
    else:
        await callback.answer(f"Статус платежа: {status}", show_alert=True)


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
