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
    print(
        "detail_config max_batch=%s split_on_empty=%s"
        % (settings.wb_detail_max_batch, settings.wb_detail_split_on_empty)
    )
    throttle = args.throttle
    if throttle is not None and throttle <= 0:
        throttle = None

    client = WildberriesClient(
        timeout=settings.request_timeout,
        min_rating=settings.min_rating,
        min_feedbacks=settings.min_feedbacks,
        min_discount=settings.min_discount,
        page_delay_ms=throttle,
        force_html_first=args.html_first,
    )

    if args.html_first:
        print(
            "HTML via Playwright fallback: принудительный режим (медленнее, "
            "используются прокси/ротация)"
        )

    banned: List[str] = args.banned or []
    products = await client.search_products(
        query=args.query,
        min_price_rub=_parse_price(args.min),
        max_price_rub=_parse_price(args.max),
        exclude_words=banned,
        max_results=args.limit or settings.max_results,
        timeout=settings.request_timeout,
    )

    for summary in client.last_page_logs:
        html_ids = summary.get("html_ids")
        html_enriched = summary.get("html_enriched")
        proxy = summary.get("proxy") or "-"
        proxy_rotated = " proxy_rotated=1" if summary.get("proxy_rotated") else ""
        slow = bool(summary.get("slow"))
        extra_parts: list[str] = []
        if html_ids is not None:
            extra_parts.append(f"ids={html_ids} enriched={html_enriched}")
        detail_batches = summary.get("detail_batches") or summary.get("detail_chunks")
        detail_singles = summary.get("detail_singles")
        detail_retries = summary.get("detail_retries")
        detail_rotations = summary.get("detail_rotations")
        detail_time_ms = summary.get("detail_time_ms")
        detail_final_chunk = summary.get("detail_final_chunk")
        detail_enriched_total = summary.get("detail_enriched_total")
        detail_used_v4 = summary.get("detail_used_v4")
        if (
            detail_batches is not None
            or detail_singles is not None
            or detail_retries is not None
            or detail_final_chunk is not None
            or detail_time_ms is not None
            or detail_enriched_total is not None
            or detail_used_v4
        ):
            detail_parts = []
            if detail_batches is not None:
                detail_parts.append(f"batches={detail_batches}")
            if detail_singles is not None:
                detail_parts.append(f"singles={detail_singles}")
            if detail_retries is not None:
                detail_parts.append(f"retries={detail_retries}")
            if detail_rotations:
                detail_parts.append(f"rotations={detail_rotations}")
            if detail_time_ms is not None:
                detail_parts.append(f"time={int(detail_time_ms)}ms")
            if detail_final_chunk is not None:
                detail_parts.append(f"final_chunk={detail_final_chunk}")
            if detail_enriched_total is not None:
                detail_parts.append(f"enriched={detail_enriched_total}")
            if detail_used_v4:
                detail_parts.append("used_v4=1")
            extra_parts.append("detail:" + " ".join(detail_parts))
        if summary.get("detail_dom_only"):
            extra_parts.append("detail_dom_only=1")
        price_before = summary.get("price_sample_before")
        price_after = summary.get("price_sample_after")
        if price_before:
            extra_parts.append(f"price_before={price_before}")
        if price_after:
            extra_parts.append(f"price_after={price_after}")
        extra = f" {' '.join(extra_parts)}" if extra_parts else ""
        html_status = summary.get("html_status")
        print(
            "source={source} page={page} status={status} html_status={html_status} products={products} "
            "limit={limit} dest={dest} spp={spp} cache={cache} slow={slow} "
            "timing={timing:.0f}ms{extra} proxy={proxy}{proxy_rotated}".format(
                page=summary.get("page"),
                source=summary.get("source"),
                status=summary.get("status"),
                html_status=html_status,
                products=summary.get("products"),
                limit=summary.get("limit"),
                dest=summary.get("dest"),
                spp=summary.get("spp"),
                cache=summary.get("cache"),
                slow=slow,
                timing=summary.get("timing_ms", 0.0),
                extra=extra,
                proxy=proxy,
                proxy_rotated=proxy_rotated,
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
        default=8,
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
        default=2000.0,
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
