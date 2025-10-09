#!/usr/bin/env python3
"""Утилита для проверки выдачи Wildberries из Telegram-бота."""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys
from typing import List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bot.config import get_settings
from bot.services.wildberries import WildberriesClient


def _parse_price(value: str | None) -> int | None:
    if value is None:
        return None
    cleaned = value.strip().replace(" ", "").replace("_", "").replace(",", ".")
    if not cleaned:
        return None
    try:
        decimal_value = Decimal(cleaned)
    except (InvalidOperation, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"Неверный формат цены: {value!r}") from exc
    if decimal_value <= 0:
        return None
    return int(decimal_value)


async def _run(args: argparse.Namespace) -> None:
    settings = get_settings()
    client = WildberriesClient(
        timeout=settings.request_timeout,
        min_rating=settings.min_rating,
        min_feedbacks=settings.min_feedbacks,
        min_discount=settings.min_discount,
    )

    banned: List[str] = args.banned or []
    products = await client.search_products(
        query=args.query,
        min_price=_parse_price(args.min),
        max_price=_parse_price(args.max),
        banned_words=banned,
        max_results=args.limit or settings.max_results,
        timeout=settings.request_timeout,
    )

    if not products:
        print("Ничего не найдено")
        return

    print(f"Найдено товаров: {len(products)}")
    for idx, product in enumerate(products, start=1):
        parts = [
            f"#{idx}",
            f"id={product.id}",
            f"{product.name}",
            f"цена={product.price}₽",
        ]
        if product.discount is not None:
            parts.append(f"скидка={product.discount}%")
        if product.score is not None:
            parts.append(f"score={product.score}")
        if product.rating is not None:
            parts.append(f"rating={product.rating}")
        if product.reviews is not None:
            parts.append(f"reviews={product.reviews}")
        print(" | ".join(parts))
        print(f"  URL: {product.url}")
        if product.image_url:
            print(f"  image: {product.image_url}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Проверка поискового клиента Wildberries",
    )
    parser.add_argument("--query", required=True, help="поисковый запрос")
    parser.add_argument("--min", default=None, help="минимальная цена в рублях")
    parser.add_argument("--max", default=None, help="максимальная цена в рублях")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="количество результатов",
    )
    parser.add_argument(
        "--banned",
        nargs="*",
        default=None,
        help="список стоп-слов, которые нужно исключить",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
