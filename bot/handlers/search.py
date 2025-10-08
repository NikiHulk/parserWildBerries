from __future__ import annotations

import httpx
from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from ..config import get_settings
from ..services.subscription import SubscriptionService
from ..services.wildberries import WildberriesClient

router = Router()


class SearchStates(StatesGroup):
    waiting_for_query = State()
    waiting_for_price = State()


async def _ensure_subscription(message: Message, subscription: SubscriptionService) -> bool:
    if subscription.has_active_subscription(message.from_user.id):
        return True

    await message.answer(
        "Поиск доступен только для активных подписчиков. Используйте команду /subscribe, чтобы оформить подписку."
    )
    return False


@router.message(Command("search"))
async def search_command(message: Message, state: FSMContext, subscription: SubscriptionService) -> None:
    if not await _ensure_subscription(message, subscription):
        return

    await state.set_state(SearchStates.waiting_for_query)
    await message.answer("Введите название товара, который хотите найти на Wildberries.")


@router.message(SearchStates.waiting_for_query)
async def process_query(message: Message, state: FSMContext) -> None:
    query = message.text.strip()
    if not query:
        await message.answer("Название не может быть пустым. Попробуйте ещё раз.")
        return

    await state.update_data(query=query)
    await state.set_state(SearchStates.waiting_for_price)
    await message.answer("Укажите минимальную цену (в рублях). Например: 1500")


@router.message(SearchStates.waiting_for_price)
async def process_price(
    message: Message,
    state: FSMContext,
    subscription: SubscriptionService,
) -> None:
    text = message.text.replace(",", ".")
    try:
        min_price = float(text)
        if min_price < 0:
            raise ValueError
    except ValueError:
        await message.answer("Не удалось понять цену. Введите число, например 999.99")
        return

    data = await state.get_data()
    query = data.get("query", "")

    settings = get_settings()
    client = WildberriesClient(timeout=settings.request_timeout)

    try:
        products = await client.search(
            query=query,
            min_price=min_price,
            limit=settings.max_results,
        )
    except httpx.HTTPError:
        await message.answer(
            "Не удалось получить данные от Wildberries. Попробуйте позже или измените запрос."
        )
        await state.clear()
        return

    if not products:
        await message.answer("Товары по указанным фильтрам не найдены.")
    else:
        lines = [
            "Найденные товары:",
            *[
                f"{idx}. {product.name} ({product.brand}) — {product.price:.2f} ₽\n{product.url}"
                for idx, product in enumerate(products, start=1)
            ],
        ]
        await message.answer("\n\n".join(lines))

    await state.clear()
