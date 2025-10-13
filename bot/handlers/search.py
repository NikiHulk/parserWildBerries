from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN

import httpx
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

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


def _parse_price_to_int(raw_value: str) -> int:
    cleaned = raw_value.replace(" ", "").replace("_", "").replace(",", ".")
    try:
        decimal_value = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        raise ValueError from None

    if decimal_value < 0:
        raise ValueError

    return int(decimal_value.quantize(Decimal("1"), rounding=ROUND_DOWN))


class SearchStates(StatesGroup):
    waiting_for_query = State()
    waiting_for_min_price = State()
    waiting_for_max_price = State()


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
    await state.set_state(SearchStates.waiting_for_min_price)
    await message.answer(
        "Укажите минимальную цену (в рублях). Например: 1500.\n"
        "Если нижний порог не нужен, отправьте 0 или напишите «Пропустить».\n"
        "Если хотите отменить поиск, нажмите «⬅️ Отмена».",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_keyboard(),
    )


@router.message(SearchStates.waiting_for_min_price)
async def process_min_price(
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

    raw_value = message.text or ""
    normalized = raw_value.strip()
    if normalized and normalized.casefold() not in {"пропустить", "skip"}:
        try:
            parsed = _parse_price_to_int(normalized)
        except ValueError:
            await message.answer(
                "Не удалось понять цену. Введите целое число, например 999 или 2 000.",
                parse_mode=ParseMode.HTML,
            )
            return
        min_price: int | None = parsed if parsed > 0 else None
    else:
        min_price = None

    await state.update_data(min_price=min_price)
    await state.set_state(SearchStates.waiting_for_max_price)
    await message.answer(
        "Укажите максимальную цену (в рублях). Например: 5000.\n"
        "Если верхний порог не нужен, отправьте 0 или напишите «Пропустить».",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_keyboard(),
    )


@router.message(SearchStates.waiting_for_max_price)
async def process_max_price(
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

    raw_text = (message.text or "").strip()
    max_price: int | None = None
    if raw_text:
        if raw_text.casefold() not in {"пропустить", "skip"}:
            try:
                parsed = _parse_price_to_int(raw_text)
            except ValueError:
                await message.answer(
                    "Не удалось понять максимальную цену. Введите целое число или 0, чтобы пропустить порог.",
                    parse_mode=ParseMode.HTML,
                )
                return
            if parsed > 0:
                max_price = parsed

    data = await state.get_data()
    query = str(data.get("query", ""))
    stored_min = data.get("min_price")
    min_price = int(stored_min) if stored_min not in (None, "") else None

    settings = get_settings()
    client = WildberriesClient(
        timeout=settings.request_timeout,
        min_rating=settings.min_rating,
        min_feedbacks=settings.min_feedbacks,
        min_discount=settings.min_discount,
    )

    user_banned = banned_words.list_words(message.from_user.id)
    try:
        products = await client.search_products(
            query=query,
            min_price_rub=min_price,
            max_price_rub=max_price,
            exclude_words=user_banned,
            max_results=settings.max_results,
            timeout=settings.request_timeout,
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

            if product.image_url:
                try:
                    await message.answer_photo(
                        product.image_url,
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
