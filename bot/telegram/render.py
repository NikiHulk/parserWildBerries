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
    try:
        return float(price_units) / 100.0
    except (TypeError, ValueError):  # noqa: BLE001
        return 0.0


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
    wallet_price = product.get("price_wb_wallet") or product.get("wallet_price")
    if wallet_price is None:
        wallet_price = _sale_price(product)
    sale_price = wallet_price
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

    if target_buy_price is None:
        target_buy_price = (
            product.get("best_buy_price")
            or product.get("best_buyout_price")
            or None
        )

    profit_rub = product.get("profit_rub")
    pct = product.get("profit_percent")
    if profit_rub is None or pct is None:
        if target_buy_price:
            profit_rub = (target_buy_price or 0) - (sale_price or 0)
            if target_buy_price and target_buy_price > 0:
                pct = (profit_rub / target_buy_price) * 100.0
            else:
                pct = None
        else:
            profit_rub = None
            pct = None

    lines: list[str] = []
    url = product.get("url") or ""
    lines.append(f"<a href='{url}'>{name}</a>")
    lines.append("")
    lines.append("<b>Карточка товара:</b>")
    lines.append(f"Цена с WB кошельком: {_fmt_int(sale_price)}")
    if target_buy_price:
        lines.append(f"Лучшая цена скупки: {_fmt_int(target_buy_price)}")
        if profit_rub is not None:
            pct_value = round(pct or 0, 1) if pct is not None else "—"
            lines.append(
                f"Профит: {_fmt_int(profit_rub)}р или {pct_value}%"
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
