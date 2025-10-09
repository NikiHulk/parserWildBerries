from __future__ import annotations

import httpx
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest

from ..config import get_settings
from ..keyboards import (
    CANCEL_BUTTON,
    SEARCH_BUTTON,
    cancel_keyboard,
    main_menu_keyboard,
)
from ..services.banned_words import BannedWordsService
from ..services.wildberries import WildberriesClient

router = Router()


class SearchStates(StatesGroup):
    waiting_for_query = State()
    waiting_for_price = State()


@router.message(Command("search"))
@router.message(F.text.casefold() == SEARCH_BUTTON.casefold())
async def search_command(message: Message, state: FSMContext) -> None:
    await state.set_state(SearchStates.waiting_for_query)
    await message.answer(
        "Введите название товара, который хотите найти на Wildberries.\n"
        "При необходимости нажмите «⬅️ Отмена», чтобы вернуться в меню.",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_keyboard(),
    )


@router.message(SearchStates.waiting_for_query)
async def process_query(message: Message, state: FSMContext) -> None:
    if message.text and message.text.casefold() == CANCEL_BUTTON.casefold():
        await state.clear()
        await message.answer(
            "Поиск отменён. Что хотите сделать дальше?",
            reply_markup=main_menu_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    query = (message.text or "").strip()
    if not query:
        await message.answer(
            "Название не может быть пустым. Попробуйте ещё раз.",
            parse_mode=ParseMode.HTML,
        )
        return

    await state.update_data(query=query)
    await state.set_state(SearchStates.waiting_for_price)
    await message.answer(
        "Укажите минимальную цену (в рублях). Например: 1500.\n"
        "Если хотите отменить поиск, нажмите «⬅️ Отмена».",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_keyboard(),
    )


@router.message(SearchStates.waiting_for_price)
async def process_price(
    message: Message,
    state: FSMContext,
    banned_words: BannedWordsService,
) -> None:
    if message.text and message.text.casefold() == CANCEL_BUTTON.casefold():
        await state.clear()
        await message.answer(
            "Поиск отменён. Вы можете выбрать новое действие из меню.",
            reply_markup=main_menu_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    text = (message.text or "").replace(",", ".")
    try:
        min_price = float(text)
        if min_price < 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "Не удалось понять цену. Введите число, например 999.99",
            parse_mode=ParseMode.HTML,
        )
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
            exclude_words=banned_words.list_words(message.from_user.id),
        )
    except httpx.HTTPError:
        await message.answer(
            "Не удалось получить данные от Wildberries. Попробуйте позже или измените запрос.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard(),
        )
        await state.clear()
        return

    if not products:
        await message.answer(
            "Товары по указанным фильтрам не найдены.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.answer(
            f"Найдено товаров: {len(products)}",
            parse_mode=ParseMode.HTML,
        )
        for idx, product in enumerate(products, start=1):
            caption_lines = [
                f"<b>{idx}. {product.name}</b>",
                product.url,
                f"Бренд: {product.brand}",
            ]

            if product.price is not None:
                caption_lines.append(f"Цена на сайте: {product.price:.2f} ₽")
            if product.wallet_price is not None:
                caption_lines.append(f"Цена с WB кошельком: {product.wallet_price:.2f} ₽")
            if product.best_buyout_price is not None:
                caption_lines.append(
                    f"Лучшая цена скупки: {product.best_buyout_price:.2f} ₽"
                )
            if product.profit_rub is not None and product.profit_percent is not None:
                caption_lines.append(
                    f"Профит: {product.profit_rub:.2f} ₽ ({product.profit_percent:.2f}%)"
                )

            if product.rating is not None:
                caption_lines.append(f"Рейтинг товара: {product.rating:.2f}")
            if product.reviews is not None:
                caption_lines.append(f"Количество отзывов: {product.reviews}")
            if product.stock is not None:
                caption_lines.append(f"Остаток у продавца: {product.stock}")

            features_text = ", ".join(product.features) if product.features else "—"
            caption_lines.append(f"Особенности: {features_text}")

            seller_lines = [f"Продавец: {product.seller_name}"]
            if product.seller_rating is not None:
                seller_lines.append(f"рейтинг {product.seller_rating:.2f}")
            if product.seller_orders is not None:
                seller_lines.append(f"заказов: {product.seller_orders}")
            if product.seller_registration:
                seller_lines.append(f"регистрация: {product.seller_registration}")
            caption_lines.append("; ".join(seller_lines))

            caption = "\n".join(caption_lines)

            if product.photo_url:
                try:
                    await message.answer_photo(
                        product.photo_url,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                    )
                    continue
                except TelegramBadRequest:
                    # Fall back to text message if photo URL is invalid
                    pass

            await message.answer(caption, parse_mode=ParseMode.HTML)

    await state.clear()
    await message.answer(
        "Готово! Выберите следующее действие.",
        reply_markup=main_menu_keyboard(),
        parse_mode=ParseMode.HTML,
    )
