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


def _parse_price(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
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
        page_delay_ms=args.throttle,
        force_html_first=args.html_first,
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

    for summary in client.last_page_logs:
        extra = ""
        html_ids = summary.get("html_ids")
        html_enriched = summary.get("html_enriched")
        if html_ids is not None:
            extra = f" ids={html_ids} enriched={html_enriched}"
        print(
            "source={source} page={page} status={status} products={products} "
            "limit={limit} dest={dest} spp={spp} cache={cache} timing={timing:.0f}ms{extra}".format(
                page=summary.get("page"),
                source=summary.get("source"),
                status=summary.get("status"),
                products=summary.get("products"),
                limit=summary.get("limit"),
                dest=summary.get("dest"),
                spp=summary.get("spp"),
                cache=summary.get("cache"),
                timing=summary.get("timing_ms", 0.0),
                extra=extra,
            )
        )
        for item in (summary.get("top_items") or [])[:3]:
            price = item.get("price")
            price_str = f"{price}₽" if price is not None else "n/a"
            print(f"  {item.get('id')}|{price_str}|{item.get('url')}")

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
        default=10,
        help="количество результатов",
    )
    parser.add_argument(
        "--banned",
        nargs="*",
        default=[],
        help="список стоп-слов, которые нужно исключить",
    )
    parser.add_argument(
        "--throttle",
        type=float,
        default=None,
        help="задержка между страницами в миллисекундах",
    )
    parser.add_argument(
        "--html-first",
        action="store_true",
        help="игнорировать JSON-эндпоинт и сразу обращаться к HTML",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
