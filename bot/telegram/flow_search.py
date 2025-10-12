"""Button-driven Telegram flow for product search."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List

import httpx
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ..config import get_settings
from ..services.wildberries import WildberriesClient, build_image_url
from .render import build_caption
from .ui import CANCEL_BUTTON, SEARCH_BUTTON, cancel_kb, main_kb, pager_kb

router = Router()

_settings = get_settings()
PAGE_SIZE = max(1, int(_settings.tg_results_per_page))
WELCOME_TEXT = _settings.tg_welcome_text


class SearchStates(StatesGroup):
    entering_query = State()
    entering_max_price = State()
    entering_excludes = State()
    showing_results = State()


def _parse_price(value: str | None) -> int | None:
    if not value:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.lower() in {"пропустить", "skip", "нет", "-"}:
        return None
    cleaned = re.sub(r"[^0-9]", "", normalized)
    if not cleaned:
        raise ValueError
    result = int(cleaned)
    if result <= 0:
        return None
    return result


def _normalize_words(text: str | None) -> List[str]:
    if not text:
        return []
    tokens = re.split(r"[\s,;\n]+", text)
    return [token.strip().lower() for token in tokens if token.strip()]


def _product_to_payload(product: Any) -> Dict[str, Any]:
    if isinstance(product, dict):
        payload = dict(product)
    elif is_dataclass(product):
        payload = asdict(product)
    else:
        payload = product.__dict__.copy() if hasattr(product, "__dict__") else {}

    product_id = payload.get("id") or getattr(product, "id", None)
    name = payload.get("name") or getattr(product, "name", None)
    brand = payload.get("brand") or getattr(product, "brand", None)

    price = payload.get("price")
    if price is None:
        price = getattr(product, "price", None)
    wallet_price = payload.get("wallet_price") or getattr(product, "wallet_price", None)

    sale_units = None
    if price is not None:
        try:
            sale_units = int(round(float(price) * 100))
        except (TypeError, ValueError):
            sale_units = None
    price_units = None
    if wallet_price is not None:
        try:
            price_units = int(round(float(wallet_price) * 100))
        except (TypeError, ValueError):
            price_units = None

    features = payload.get("features") or getattr(product, "features", None) or []
    if isinstance(features, str):
        features = [features]
    stock = payload.get("stock")
    if stock is None:
        stock = getattr(product, "stock", None)
    seller_name = payload.get("seller_name") or getattr(product, "seller_name", None)
    seller_rating = payload.get("seller_rating") or getattr(product, "seller_rating", None)
    seller_orders = payload.get("seller_orders") or getattr(product, "seller_orders", None)
    seller_registration = (
        payload.get("seller_registration")
        or getattr(product, "seller_registration", None)
    )
    rating = payload.get("rating") or getattr(product, "rating", None)
    feedbacks = payload.get("reviews") or payload.get("feedbacks") or getattr(product, "reviews", None)

    url = payload.get("url") or getattr(product, "url", None)
    if product_id and not url:
        url = f"https://www.wildberries.ru/catalog/{product_id}/detail.aspx"

    image_url = payload.get("image_url") or getattr(product, "image_url", None)
    if product_id and not image_url:
        image_url = build_image_url(int(product_id))

    supplier = payload.get("supplier") or seller_name
    supplier_rating = payload.get("supplierRating") or seller_rating
    supplier_orders = payload.get("supplierOrders") or seller_orders
    supplier_registration = (
        payload.get("supplierRegistration") or seller_registration
    )

    return {
        "id": product_id,
        "name": name,
        "brand": brand,
        "salePriceU": sale_units,
        "priceU": price_units,
        "rating": rating,
        "feedbacks": feedbacks,
        "colors": features,
        "optionName": features[0] if features else None,
        "sizes": (
            [{"stocks": [{"qty": stock}]}]
            if stock is not None
            else []
        ),
        "stock": stock,
        "supplier": supplier,
        "supplierRating": supplier_rating,
        "supplierOrders": supplier_orders,
        "supplierRegistration": supplier_registration,
        "url": url,
        "image_url": image_url,
    }


async def _clear_previous_results(bot, chat_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    previous_ids: List[int] = data.get("product_message_ids", [])
    nav_message_id = data.get("nav_message_id")

    for message_id in previous_ids:
        try:
            await bot.delete_message(chat_id, message_id)
        except Exception:  # noqa: BLE001
            continue

    if nav_message_id:
        try:
            await bot.delete_message(chat_id, nav_message_id)
        except Exception:  # noqa: BLE001
            pass

    await state.update_data(
        product_message_ids=[],
        nav_message_id=None,
    )


async def _send_page(bot, chat_id: int, state: FSMContext, items: List[Dict[str, Any]], page: int) -> None:
    data = await state.get_data()
    previous_ids: List[int] = data.get("product_message_ids", [])
    nav_message_id = data.get("nav_message_id")

    for message_id in previous_ids:
        try:
            await bot.delete_message(chat_id, message_id)
        except Exception:  # noqa: BLE001
            continue

    total_pages = max(1, math.ceil(len(items) / PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    start_index = page * PAGE_SIZE
    end_index = start_index + PAGE_SIZE
    chunk = items[start_index:end_index]

    new_message_ids: List[int] = []
    for product in chunk:
        caption = build_caption(product)
        image_url = product.get("image_url")
        if image_url:
            sent = await bot.send_photo(
                chat_id,
                photo=image_url,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
        else:
            sent = await bot.send_message(
                chat_id,
                caption,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
            )
        new_message_ids.append(sent.message_id)

    navigation_text = (
        f"Страница {page + 1}/{total_pages}. Найдено {len(items)} товаров."
    )
    markup = pager_kb(page, total_pages) if total_pages > 1 else None

    if nav_message_id:
        try:
            await bot.edit_message_text(
                navigation_text,
                chat_id=chat_id,
                message_id=nav_message_id,
                reply_markup=markup,
            )
        except Exception:  # noqa: BLE001
            sent_nav = await bot.send_message(
                chat_id,
                navigation_text,
                reply_markup=markup,
            )
            nav_message_id = sent_nav.message_id
    else:
        sent_nav = await bot.send_message(
            chat_id,
            navigation_text,
            reply_markup=markup,
        )
        nav_message_id = sent_nav.message_id

    await state.update_data(
        product_message_ids=new_message_ids,
        nav_message_id=nav_message_id,
        results=items,
        page=page,
    )


@router.message(CommandStart())
async def handle_start(message: Message, state: FSMContext) -> None:
    await _clear_previous_results(message.bot, message.chat.id, state)
    await state.clear()
    await message.answer(WELCOME_TEXT, reply_markup=main_kb())


@router.message(F.text == SEARCH_BUTTON)
async def start_flow(message: Message, state: FSMContext) -> None:
    await _clear_previous_results(message.bot, message.chat.id, state)
    await state.set_state(SearchStates.entering_query)
    await message.answer(
        "Введите название товара:",
        reply_markup=cancel_kb(),
    )


@router.message(SearchStates.entering_query)
async def handle_query(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text == CANCEL_BUTTON:
        await state.clear()
        await message.answer(WELCOME_TEXT, reply_markup=main_kb())
        return
    if not text:
        await message.answer("Название не может быть пустым. Попробуйте ещё раз.")
        return
    await state.update_data(query=text)
    await state.set_state(SearchStates.entering_max_price)
    await message.answer(
        "Верхний порог цены (рубли). Оставьте пустым или отправьте «пропустить», если ограничение не нужно:",
        reply_markup=cancel_kb(),
    )


@router.message(SearchStates.entering_max_price)
async def handle_max_price(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text == CANCEL_BUTTON:
        await state.clear()
        await message.answer(WELCOME_TEXT, reply_markup=main_kb())
        return
    if not text:
        price = None
    else:
        try:
            price = _parse_price(text)
        except ValueError:
            await message.answer(
                "Не удалось распознать цену. Введите целое число или оставьте поле пустым.",
            )
            return
    await state.update_data(max_price=price)
    await state.set_state(SearchStates.entering_excludes)
    await message.answer(
        "Исключающие слова (через запятую). Например: б/у, восстановленный. Можно отправить пустое сообщение.",
        reply_markup=cancel_kb(),
    )


@router.message(SearchStates.entering_excludes)
async def handle_excludes(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text == CANCEL_BUTTON:
        await _clear_previous_results(message.bot, message.chat.id, state)
        await state.clear()
        await message.answer(WELCOME_TEXT, reply_markup=main_kb())
        return

    banned_words = _normalize_words(text)
    data = await state.get_data()
    query = data.get("query")
    max_price = data.get("max_price")

    settings = get_settings()
    client = WildberriesClient(
        timeout=settings.request_timeout,
        min_rating=settings.min_rating,
        min_feedbacks=settings.min_feedbacks,
        min_discount=settings.min_discount,
    )

    progress_message = await message.answer(
        "Ищу подходящие товары...",
        reply_markup=main_kb(),
    )

    try:
        products = await client.search_products(
            query=query,
            min_price=None,
            max_price=max_price,
            banned_words=banned_words,
            max_results=max(settings.max_results, PAGE_SIZE * 5),
            timeout=settings.request_timeout,
        )
    except httpx.HTTPError:
        if progress_message:
            try:
                await message.bot.delete_message(
                    message.chat.id, progress_message.message_id
                )
            except Exception:  # noqa: BLE001
                pass
        await state.clear()
        await message.answer(
            "Не удалось получить данные от Wildberries. Попробуйте позже.",
            reply_markup=main_kb(),
        )
        return

    items = [_product_to_payload(product) for product in products]

    filtered_items: List[Dict[str, Any]] = []
    for item in items:
        if max_price is not None:
            price_units = item.get("salePriceU") or item.get("priceU") or 0
            if price_units and (price_units / 100.0) > float(max_price):
                continue
        haystack = f"{item.get('name', '')} {item.get('brand', '')}".lower()
        if any(word in haystack for word in banned_words):
            continue
        filtered_items.append(item)

    if not filtered_items:
        if progress_message:
            try:
                await message.bot.delete_message(
                    message.chat.id, progress_message.message_id
                )
            except Exception:  # noqa: BLE001
                pass
        await state.clear()
        await message.answer(
            "По заданным критериям ничего не найдено.",
            reply_markup=main_kb(),
        )
        return

    filtered_items.sort(
        key=lambda item: item.get("salePriceU") or item.get("priceU") or 0,
    )

    await state.update_data(
        results=filtered_items,
        page=0,
        product_message_ids=[],
        nav_message_id=None,
    )
    await state.set_state(SearchStates.showing_results)

    await _send_page(
        bot=message.bot,
        chat_id=message.chat.id,
        state=state,
        items=filtered_items,
        page=0,
    )

    if progress_message:
        try:
            await message.bot.delete_message(
                message.chat.id, progress_message.message_id
            )
        except Exception:  # noqa: BLE001
            pass


@router.callback_query(SearchStates.showing_results, F.data.startswith("pg:"))
async def handle_pagination(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    items = data.get("results")
    if not items:
        await callback.answer("Нужно заново выполнить поиск.", show_alert=True)
        return
    try:
        page = int(callback.data.split(":", 1)[1])
    except (ValueError, AttributeError, IndexError):
        await callback.answer()
        return

    await _send_page(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        state=state,
        items=items,
        page=page,
    )
    await callback.answer()


@router.message(SearchStates.showing_results, F.text == CANCEL_BUTTON)
async def handle_cancel_in_results(message: Message, state: FSMContext) -> None:
    await _clear_previous_results(message.bot, message.chat.id, state)
    await state.clear()
    await message.answer(WELCOME_TEXT, reply_markup=main_kb())


@router.message()
async def fallback(message: Message, state: FSMContext) -> None:
    await _clear_previous_results(message.bot, message.chat.id, state)
    await state.clear()
    await message.answer(WELCOME_TEXT, reply_markup=main_kb())
