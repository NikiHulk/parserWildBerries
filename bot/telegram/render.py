"""Rendering helpers for Telegram responses."""

from __future__ import annotations

from typing import Dict, Optional


def _fmt_int(value: Optional[float]) -> str:
    if value is None:
        return "—"
    try:
        return f"{int(round(value)):,}".replace(",", " ")
    except Exception:  # noqa: BLE001
        return str(value)


def _sale_price(product: Dict) -> float:
    price_units = product.get("salePriceU") or product.get("priceU") or 0
    return float(price_units) / 100.0


def _stock(product: Dict) -> int:
    total = 0
    for size in product.get("sizes") or []:
        for stock in size.get("stocks") or []:
            try:
                total += int(stock.get("qty", 0))
            except (TypeError, ValueError):
                continue
    return total


def build_caption(product: Dict, target_buy_price: Optional[float] = None) -> str:
    name = product.get("name") or "Товар"
    sale_price = _sale_price(product)
    rating = product.get("rating") or 0
    feedbacks = product.get("feedbacks") or 0
    colors = product.get("colors") or []
    option_name = product.get("optionName")
    color = colors[0] if colors else option_name or "—"
    stock = product.get("stock")
    if stock is None:
        stock = _stock(product)

    seller = product.get("supplier") or "—"
    seller_rating = (
        product.get("supplierRating")
        or product.get("supplier_rating")
        or "—"
    )
    seller_orders = (
        product.get("supplierOrders")
        or product.get("ordersCount")
        or "—"
    )
    seller_since = (
        product.get("supplierRegistration")
        or product.get("regDate")
        or "—"
    )

    profit_rub = pct = None
    if target_buy_price:
        profit_rub = target_buy_price - sale_price
        if target_buy_price > 0:
            pct = profit_rub / target_buy_price * 100.0

    lines: list[str] = []
    url = product.get("url") or ""
    lines.append(f"<a href='{url}'>{name}</a>")
    lines.append("")
    lines.append("<b>Карточка товара:</b>")
    lines.append(f"Цена с WB кошельком: {_fmt_int(sale_price)}")
    if target_buy_price:
        lines.append(f"Лучшая цена скупки: {_fmt_int(target_buy_price)}")
        lines.append(
            f"Профит: {_fmt_int(profit_rub)}р или {round(pct or 0, 1)}%"
        )
    lines.append(f"Рейтинг товара: {rating}")
    lines.append(f"Количество отзывов: {feedbacks}")
    lines.append(f"Особенности: {color}")
    lines.append(f"Остаток: {_fmt_int(stock)}")
    lines.append("")
    lines.append("<b>Информация о продавце:</b>")
    lines.append(f"Магазин: {seller}")
    lines.append(f"Рейтинг: {seller_rating}")
    lines.append(f"Количество заказов: {seller_orders}")
    lines.append(f"Дата регистрации: {seller_since}")
    return "\n".join(lines)
