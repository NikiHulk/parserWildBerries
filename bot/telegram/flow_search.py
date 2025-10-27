"""Button-driven Telegram flow for product search."""

from __future__ import annotations

import asyncio
import logging
import math
import re
from contextlib import suppress
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ErrorEvent, Message

from ..config import get_settings
from ..services.wildberries import WildberriesClient, build_image_url, score_item
from .ui import (
    SEARCH_BUTTON,
    cancel_kb,
    cancel_skip_kb,
    main_kb,
    pager_kb,
    remove_kb,
)

logger = logging.getLogger(__name__)

router = Router()

_settings = get_settings()
PAGE_SIZE = max(1, int(_settings.tg_results_per_page))
WELCOME_TEXT = _settings.tg_welcome_text
CANCEL_TEXT = _settings.tg_cancel_text
SKIP_TEXT = _settings.tg_skip_text
TOP_K = max(1, int(_settings.tg_top_k))

_SPLIT_EXCLUDES_RE = re.compile(r"[\n,;]+")


class SearchStates(StatesGroup):
    entering_query = State()
    entering_max_price = State()
    entering_excludes = State()
    showing_results = State()


def _is_cancel(text: str | None) -> bool:
    if not text:
        return False
    normalized = text.strip().lower()
    return normalized in {
        CANCEL_TEXT.lower(),
        "отмена",
        "cancel",
        "/cancel",
    }


def _parse_price(value: str | None) -> int | None:
    if not value:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.lower() in {SKIP_TEXT.lower(), "пропустить", "skip", "нет", "-"}:
        return None
    cleaned = re.sub(r"[^0-9]", "", normalized)
    if not cleaned:
        raise ValueError
    result = int(cleaned)
    if result <= 0:
        return None
    return result


def parse_excludes(raw: str | None) -> List[str]:
    """Parses exclude list safely, ignoring short/empty tokens."""

    if not raw:
        return []
    items = [token.strip().lower() for token in _SPLIT_EXCLUDES_RE.split(raw)]
    banned_symbols = {".", ",", "-", "_", "—", "–", "/", "\\"}
    return [
        token
        for token in items
        if token
        and token not in banned_symbols
        and len(token) > 1
    ]


async def _progress(bot, chat_id: int, interval: int) -> None:
    interval = max(1, interval)
    try:
        while True:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:  # pragma: no cover - cooperative cancellation
        return


def _product_matches_excludes(product: Any, excludes: List[str]) -> bool:
    if not excludes:
        return False

    parts: List[str] = []
    for attr in ("name", "brand"):
        value = getattr(product, attr, None)
        if isinstance(value, str):
            parts.append(value)

    features = getattr(product, "features", None)
    if isinstance(features, list):
        parts.extend(str(item) for item in features if item)

    hay = " ".join(parts).lower()
    return any(ex in hay for ex in excludes)


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
    wallet_price = (
        payload.get("wallet_price")
        or getattr(product, "wallet_price", None)
        or getattr(product, "price_wb_wallet", None)
    )
    best_buy_price = (
        payload.get("best_buy_price")
        or getattr(product, "best_buyout_price", None)
        or getattr(product, "best_buy_price", None)
    )

    sale_units = None
    if price is not None:
        try:
            sale_units = int(round(float(price) * 100))
        except (TypeError, ValueError):
            sale_units = None
    price_units = None
    wallet_price_units = None
    if wallet_price is not None:
        try:
            wallet_price_units = int(round(float(wallet_price) * 100))
        except (TypeError, ValueError):
            wallet_price_units = None
    price_units = wallet_price_units

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

    profit_rub = payload.get("profit_rub") or getattr(product, "profit_rub", None)
    profit_percent = (
        payload.get("profit_percent")
        or getattr(product, "profit_percent", None)
    )

    stock_total = payload.get("stock")
    if stock_total is None:
        stock_total = getattr(product, "stock", None)

    return {
        "id": product_id,
        "name": name,
        "brand": brand,
        "salePriceU": sale_units,
        "priceU": price_units,
        "wallet_price": wallet_price,
        "price_wb_wallet": wallet_price,
        "best_buy_price": best_buy_price,
        "best_buyout_price": best_buy_price,
        "profit_rub": profit_rub,
        "profit_percent": profit_percent,
        "rating": rating,
        "feedbacks": feedbacks,
        "colors": features,
        "optionName": features[0] if features else None,
        "sizes": (
            [{"stocks": [{"qty": stock}]}]
            if stock is not None
            else []
        ),
        "stock": stock_total,
        "supplier": supplier,
        "supplierRating": supplier_rating,
        "supplierOrders": supplier_orders,
        "supplierRegistration": supplier_registration,
        "url": url,
        "image_url": image_url,
        "score": payload.get("score") or getattr(product, "score", None),
    }


def _format_card_text(item: Dict[str, Any], rank: int) -> str:
    price = item.get("_price_rub")
    price_str = f"{price:.0f}₽" if isinstance(price, (int, float)) else "—"

    score = item.get("_score")
    score_str = f"{score:.4f}" if isinstance(score, (int, float)) else "—"

    rating = item.get("rating")
    rating_str = f"{rating:.1f}" if isinstance(rating, (int, float)) else "—"

    reviews = item.get("feedbacks") or item.get("reviews") or 0
    try:
        reviews_str = str(int(reviews))
    except (TypeError, ValueError):
        reviews_str = str(reviews) if reviews is not None else "0"

    name = item.get("name") or item.get("title") or "Без названия"
    url = (
        item.get("detail_url")
        or item.get("url")
        or f"https://www.wildberries.ru/catalog/{item.get('id')}/detail.aspx"
    )

    lines = [
        f"#{rank} | id={item.get('id')} | {name}",
        f"цена={price_str} | score={score_str} | rating={rating_str} | reviews={reviews_str}",
        f"URL: {url}",
    ]
    return "\n".join(lines).strip()


async def _clear_previous_results(bot, chat_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    previous_ids: List[int] = data.get("product_message_ids", [])
    nav_message_id = data.get("nav_message_id")
    menu_message_id = data.get("menu_message_id")

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

    if menu_message_id:
        try:
            await bot.delete_message(chat_id, menu_message_id)
        except Exception:  # noqa: BLE001
            pass

    await state.update_data(
        product_message_ids=[],
        nav_message_id=None,
        menu_message_id=None,
    )


async def _send_page(bot, chat_id: int, state: FSMContext, items: List[Dict[str, Any]], page: int) -> None:
    data = await state.get_data()
    previous_ids: List[int] = data.get("product_message_ids", [])
    nav_message_id = data.get("nav_message_id")
    menu_message_id = data.get("menu_message_id")

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
    for index, product in enumerate(chunk, start=1):
        text = _format_card_text(product, index)
        try:
            sent = await bot.send_message(
                chat_id,
                text,
                disable_web_page_preview=False,
            )
        except Exception as exc:  # noqa: BLE001
            fallback_text = (
                f"{text}\n\n(Предупреждение: не удалось отправить сообщение как задумано: {exc})"
            )
            sent = await bot.send_message(
                chat_id,
                fallback_text,
                disable_web_page_preview=True,
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
        menu_message_id=menu_message_id,
    )

    if not menu_message_id:
        menu_message = await bot.send_message(
            chat_id,
            "Вы можете начать новый поиск через меню ниже.",
            reply_markup=main_kb(),
        )
        await state.update_data(menu_message_id=menu_message.message_id)


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
        f"Введите название товара (для отмены — «{CANCEL_TEXT}»):",
        reply_markup=cancel_kb(CANCEL_TEXT),
    )


@router.message(SearchStates.entering_query)
async def handle_query(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if _is_cancel(text):
        await state.clear()
        await message.answer(WELCOME_TEXT, reply_markup=main_kb())
        return
    if not text:
        await message.answer(
            "Название не может быть пустым. Попробуйте ещё раз.",
            reply_markup=cancel_kb(CANCEL_TEXT),
        )
        return
    await state.update_data(query=text)
    await state.set_state(SearchStates.entering_max_price)
    await message.answer(
        (
            "Верхний порог цены (рубли). Можно пропустить, отправив пустое сообщение"
            f" или кнопку «{SKIP_TEXT}». Для отмены — «{CANCEL_TEXT}»."
        ),
        reply_markup=cancel_kb(CANCEL_TEXT),
    )


@router.message(SearchStates.entering_max_price)
async def handle_max_price(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if _is_cancel(text):
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
                reply_markup=cancel_kb(CANCEL_TEXT),
            )
            return
    await state.update_data(max_price_rub=price)
    await state.set_state(SearchStates.entering_excludes)
    await message.answer(
        (
            "Исключающие слова (через запятую). Например: б/у, восстановленный."
            f" Можно пропустить через кнопку «{SKIP_TEXT}». Для отмены — «{CANCEL_TEXT}»."
        ),
        reply_markup=cancel_skip_kb(CANCEL_TEXT, SKIP_TEXT),
    )


@router.message(SearchStates.entering_excludes)
async def handle_excludes(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    lowered = text.lower()
    if _is_cancel(text):
        await _clear_previous_results(message.bot, message.chat.id, state)
        await state.clear()
        await message.answer(WELCOME_TEXT, reply_markup=main_kb())
        return

    skip_tokens = {SKIP_TEXT.lower(), "пропустить", "skip", "-", "нет"}
    if not text or lowered in skip_tokens:
        banned_words: List[str] = []
    else:
        banned_words = parse_excludes(text)
    data = await state.get_data()
    query = data.get("query")
    max_price_rub = data.get("max_price_rub")

    settings = get_settings()
    client = WildberriesClient(
        timeout=settings.request_timeout,
        min_rating=settings.min_rating,
        min_feedbacks=settings.min_feedbacks,
        min_discount=settings.min_discount,
    )

    await _clear_previous_results(message.bot, message.chat.id, state)

    status_message = await message.answer(
        settings.tg_progress_text,
        reply_markup=remove_kb(),
    )

    progress_task = asyncio.create_task(
        _progress(
            message.bot,
            message.chat.id,
            settings.tg_progress_interval,
        )
    )

    products: List[Any] = []

    try:
        async with asyncio.timeout(settings.tg_search_timeout):
            products = await client.search_products(
                query=query,
                max_price_rub=max_price_rub,
                exclude_words=banned_words,
                top_k=TOP_K,
                max_results=max(settings.max_results, PAGE_SIZE * 5),
                timeout=settings.request_timeout,
                max_pages=settings.tg_search_max_pages,
            )
    except asyncio.TimeoutError:
        logger.warning("TG search timeout: query=%r", query)
        await state.clear()
        await message.answer(
            "⏳ Поиск занял слишком много времени. Попробуйте снизить порог цены, убрать исключения или повторить позже.",
            reply_markup=main_kb(),
        )
        products = []
    except Exception as exc:  # noqa: BLE001
        logger.exception("TG search failed: query=%r", query, exc_info=exc)
        await state.clear()
        await message.answer(
            "⚠️ Произошла ошибка при поиске. Попробуйте ещё раз.",
            reply_markup=main_kb(),
        )
        products = []
    finally:
        progress_task.cancel()
        with suppress(asyncio.CancelledError):
            await progress_task
        if status_message:
            try:
                await message.bot.delete_message(
                    message.chat.id, status_message.message_id
                )
            except Exception:  # noqa: BLE001
                pass

    if not products:
        await state.clear()
        await message.answer(
            "По заданным критериям ничего не найдено. Попробуйте увеличить верхний порог цены или убрать часть исключений.",
            reply_markup=main_kb(),
        )
        return

    payloads = [_product_to_payload(product) for product in products]
    items = sorted(payloads, key=score_item)[:TOP_K]

    await state.update_data(
        results=items,
        page=0,
        product_message_ids=[],
        nav_message_id=None,
    )
    await state.set_state(SearchStates.showing_results)

    await _send_page(
        bot=message.bot,
        chat_id=message.chat.id,
        state=state,
        items=items,
        page=0,
    )


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


@router.message(SearchStates.showing_results, F.text.casefold() == CANCEL_TEXT.lower())
async def handle_cancel_in_results(message: Message, state: FSMContext) -> None:
    await _clear_previous_results(message.bot, message.chat.id, state)
    await state.clear()
    await message.answer(WELCOME_TEXT, reply_markup=main_kb())


@router.errors()
async def handle_flow_error(event: ErrorEvent) -> None:
    logger.exception("Unhandled error in Telegram flow", exc_info=event.exception)
    update = event.update
    reply_text = "⚠️ Произошла ошибка. Попробуйте ещё раз."
    if update.message:
        await update.message.answer(reply_text, reply_markup=main_kb())
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.answer(reply_text, reply_markup=main_kb())
    event.handled = True


@router.message()
async def fallback(message: Message, state: FSMContext) -> None:
    await _clear_previous_results(message.bot, message.chat.id, state)
    await state.clear()
    await message.answer(WELCOME_TEXT, reply_markup=main_kb())
