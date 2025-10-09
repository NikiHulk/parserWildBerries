from __future__ import annotations

import asyncio
import importlib.util
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, List
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger(__name__)

DETAIL_API_URL = "https://card.wb.ru/cards/detail"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru,en;q=0.9",
    "Referer": "https://www.wildberries.ru/",
    "Connection": "keep-alive",
}

WB_REGIONS = "80,64,38,4,115,83,33,68,70,86,75,30,40,48,69,22,66,31,1,114"
DESTS = [-1257786, -1069100, -1044448]
SPPS = [30, 0]

NM_RE = re.compile(r'data-nm-id="(\d+)"')


def url_catalog(query: str, page: int, limit: int, dest: int, spp: int) -> str:
    return (
        "https://catalog.wb.ru/catalog/0/search"
        f"?appType=1&curr=rub&dest={dest}&spp={spp}"
        f"&regions={WB_REGIONS}&page={page}&limit={limit}&query={quote_plus(query)}"
    )


def url_exactmatch(query: str, page: int, limit: int, dest: int, spp: int) -> str:
    return (
        "https://search.wb.ru/exactmatch/ru/common/v4/search"
        f"?appType=1&curr=rub&dest={dest}&spp={spp}&resultset=catalog"
        f"&regions={WB_REGIONS}&page={page}&limit={limit}&query={quote_plus(query)}"
    )


def url_catalog_alt(query: str, page: int, limit: int, dest: int, spp: int) -> str:
    return (
        "https://wbxcatalog-ru.wildberries.ru/catalog/0/search"
        f"?appType=1&curr=rub&dest={dest}&spp={spp}"
        f"&regions={WB_REGIONS}&page={page}&limit={limit}&query={quote_plus(query)}"
    )


def url_html_search(query: str, page: int) -> str:
    return (
        "https://www.wildberries.ru/catalog/0/search.aspx"
        f"?search={quote_plus(query)}&page={page}"
    )


def parse_products_json(
    response: httpx.Response, url: str, source: str
) -> list[dict[str, Any]]:
    try:
        payload = response.json()
    except Exception:
        logger.error(
            "non-JSON from WB (%s) %s status=%s body[:200]=%r",
            source,
            url,
            response.status_code,
            (response.text or "")[:200],
        )
        return []

    data = payload.get("data") or {}
    products = data.get("products") or []
    if isinstance(products, Sequence):
        return [item for item in products if isinstance(item, dict)]
    return []


def build_image_url(nm_id: int) -> str:
    """Формирует прямую ссылку на изображение карточки Wildberries."""

    vol = nm_id // 100000
    part = nm_id // 1000
    host = (nm_id // 100000) % 10
    return f"https://basket-0{host}.wb.ru/vol{vol}/part{part}/{nm_id}/images/big/1.jpg"


@dataclass(slots=True)
class Product:
    id: int
    name: str
    brand: str
    price: float | None
    wallet_price: float | None
    best_buyout_price: float | None
    discount: float | None
    profit_rub: float | None
    profit_percent: float | None
    rating: float | None
    reviews: int | None
    features: List[str]
    stock: int | None
    seller_name: str
    seller_rating: float | None
    seller_orders: int | None
    seller_registration: str | None
    url: str
    image_url: str | None
    score: float | None = None


class WildberriesClient:
    """Client for fetching products from Wildberries search API."""

    def __init__(
        self,
        timeout: float = 10.0,
        *,
        user_agent: str | None = None,
        min_rating: float | None = None,
        min_feedbacks: int | None = None,
        min_discount: float | None = None,
    ) -> None:
        self._timeout = timeout
        self._headers = dict(HEADERS)
        if user_agent:
            self._headers["User-Agent"] = user_agent
        self._http2_enabled = self._detect_http2_support()
        self._min_rating = min_rating
        self._min_feedbacks = min_feedbacks
        self._min_discount = min_discount

    @staticmethod
    def _detect_http2_support() -> bool:
        """Определяем, доступна ли поддержка HTTP/2 (есть ли пакет h2)."""

        if importlib.util.find_spec("h2") is None:
            logger.warning(
                "HTTP/2 недоступен: пакет 'h2' не установлен. Запросы Wildberries будут "
                "выполняться по HTTP/1.1"
            )
            return False
        return True

    async def search_products(
        self,
        query: str,
        min_price: int | None,
        max_price: int | None,
        banned_words: list[str],
        max_results: int,
        timeout: int,
    ) -> List[Product]:
        if max_results <= 0:
            return []

        min_price_rub: int | None = None
        if min_price is not None:
            try:
                min_candidate = int(min_price)
            except (TypeError, ValueError):
                min_candidate = None
            else:
                min_candidate = max(min_candidate, 0)
                min_price_rub = min_candidate or None

        max_price_rub: int | None = None
        if max_price not in (None, 0):
            try:
                max_candidate = int(max_price)
            except (TypeError, ValueError):
                max_candidate = None
            else:
                max_candidate = max(max_candidate, 0)
                max_price_rub = max_candidate or None

        if (
            min_price_rub is not None
            and max_price_rub is not None
            and min_price_rub > max_price_rub
        ):
            min_price_rub, max_price_rub = max_price_rub, min_price_rub

        normalized_banned = {
            word.strip().lower()
            for word in banned_words
            if isinstance(word, str) and word.strip()
        }

        filtered_candidates: list[dict[str, Any]] = []
        filtered_by_price = 0
        filtered_by_banned = 0
        filtered_by_rating = 0
        filtered_by_feedbacks = 0
        filtered_by_discount = 0
        total_received = 0

        effective_timeout = float(timeout or self._timeout)

        async with httpx.AsyncClient(
            timeout=effective_timeout,
            headers=self._headers,
            follow_redirects=True,
            http2=self._http2_enabled,
        ) as client:
            page = 1

            while len(filtered_candidates) < max_results:
                (
                    page_products,
                    source,
                    _url,
                    used_dest,
                    used_spp,
                ) = await self._fetch_page(
                    client,
                    query=query,
                    page=page,
                    limit=100,
                    timeout=effective_timeout,
                )

                raw_products = page_products or []
                products_list: list[dict[str, Any]] = [
                    item if isinstance(item, dict) else dict(item)
                    for item in raw_products
                ]
                total_received += len(products_list)

                if not products_list:
                    break

                await self._enrich_products_from_details(
                    products_list,
                    timeout=effective_timeout,
                )

                before_count = len(products_list)
                page_price_filtered = 0
                page_banned_filtered = 0
                page_rating_filtered = 0
                page_feedback_filtered = 0
                page_discount_filtered = 0

                page_candidates: list[dict[str, Any]] = []
                for item in products_list:
                    product_id = item.get("id")
                    if product_id is None:
                        continue

                    price_units = item.get("salePriceU") or item.get("priceU")
                    price_rub = self._price_units_to_rub(price_units)

                    if (
                        min_price_rub is not None
                        and (price_rub is None or price_rub < min_price_rub)
                    ):
                        filtered_by_price += 1
                        page_price_filtered += 1
                        continue

                    if (
                        max_price_rub is not None
                        and (price_rub is None or price_rub > max_price_rub)
                    ):
                        filtered_by_price += 1
                        page_price_filtered += 1
                        continue

                    if normalized_banned:
                        haystack = (
                            f"{item.get('name', '')} {item.get('brand', '')}"
                        ).lower()
                        if any(word in haystack for word in normalized_banned):
                            filtered_by_banned += 1
                            page_banned_filtered += 1
                            continue

                    rating = self._safe_float(item.get("reviewRating"))
                    if (
                        self._min_rating is not None
                        and rating is not None
                        and rating < self._min_rating
                    ):
                        filtered_by_rating += 1
                        page_rating_filtered += 1
                        continue

                    feedbacks = self._safe_int(item.get("feedbacks"))
                    if (
                        self._min_feedbacks is not None
                        and feedbacks is not None
                        and feedbacks < self._min_feedbacks
                    ):
                        filtered_by_feedbacks += 1
                        page_feedback_filtered += 1
                        continue

                    discount_percent = self._compute_discount_percent(item)
                    if (
                        self._min_discount is not None
                        and discount_percent is not None
                        and discount_percent < self._min_discount
                    ):
                        filtered_by_discount += 1
                        page_discount_filtered += 1
                        continue

                    candidate = {
                        "item": item,
                        "price_rub": price_rub,
                        "rating": rating,
                        "feedbacks": feedbacks,
                        "discount": discount_percent,
                    }
                    page_candidates.append(candidate)

                    if len(filtered_candidates) + len(page_candidates) >= max_results:
                        break

                filtered_candidates.extend(page_candidates)

                after_price_count = before_count - page_price_filtered
                after_banned_count = after_price_count - page_banned_filtered
                after_quality_count = len(page_candidates)

                logger.info(
                    "Страница %s (%s, dest=%s, spp=%s): before=%s after_price=%s "
                    "after_banned=%s after_quality=%s total=%s",
                    page,
                    source,
                    used_dest,
                    used_spp,
                    before_count,
                    after_price_count,
                    after_banned_count,
                    after_quality_count,
                    len(filtered_candidates),
                )

                if len(filtered_candidates) >= max_results:
                    break

                page += 1

        if not filtered_candidates:
            reason_parts = []
            if total_received == 0:
                reason_parts.append("empty API")
            if filtered_by_price:
                reason_parts.append("min/max")
            if filtered_by_banned:
                reason_parts.append("banned words")
            if filtered_by_rating:
                reason_parts.append("rating")
            if filtered_by_feedbacks:
                reason_parts.append("feedbacks")
            if filtered_by_discount:
                reason_parts.append("discount")
            reason = ", ".join(reason_parts) or "unknown"
            logger.info("После фильтрации товаров нет (reason: %s)", reason)
            return []

        self._apply_scores(filtered_candidates)
        filtered_candidates.sort(
            key=lambda c: (
                c.get("score") is not None,
                c.get("score") or 0.0,
                c.get("discount") or 0.0,
                c.get("rating") or 0.0,
                -(c.get("price_rub") or 0),
            ),
            reverse=True,
        )

        logger.info(
            "После фильтрации: %s товаров (reason=success)",
            len(filtered_candidates),
        )

        limited_candidates = filtered_candidates[:max_results]
        detail_map = await self._fetch_details(
            (int(candidate["item"]["id"]) for candidate in limited_candidates),
            timeout=effective_timeout,
        )

        products: List[Product] = []
        for candidate in limited_candidates:
            item = candidate["item"]
            product_id = int(item["id"])
            detail = detail_map.get(product_id, {})
            features = self._extract_features(detail)
            stock = self._extract_stock(detail)
            registration = self._extract_registration(detail)

            product = self._build_product(
                item,
                detail,
                features=features,
                stock=stock,
                registration=registration,
                discount=candidate.get("discount"),
            )
            product.score = candidate.get("score")
            products.append(product)

        return products

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        *,
        query: str,
        page: int,
        limit: int,
        timeout: float,
    ) -> tuple[list[dict[str, Any]], str, str, int | None, int | None]:
        for dest in DESTS:
            for spp in SPPS:
                for builder, label in (
                    (url_catalog, "catalog"),
                    (url_catalog_alt, "catalog_alt"),
                    (url_exactmatch, "exactmatch"),
                ):
                    url = builder(query, page, limit, dest, spp)
                    for attempt in range(1, 4):
                        try:
                            response = await client.get(url, timeout=timeout)
                        except (httpx.ReadTimeout, httpx.ConnectError) as exc:
                            logger.warning(
                                "WB %s attempt %s failed: %s",
                                label,
                                attempt,
                                exc,
                            )
                            await asyncio.sleep(0.3)
                            continue

                        products = parse_products_json(response, url, label)
                        logger.info(
                            "WB запрос (%s): %s -> %s, products=%s (dest=%s,spp=%s)",
                            label,
                            url,
                            response.status_code,
                            len(products),
                            dest,
                            spp,
                        )
                        if products:
                            preview = products[0]
                            sale_price_u = preview.get("salePriceU")
                            price_u = preview.get("priceU")
                            price_rub_preview = self._price_units_to_rub(
                                sale_price_u or price_u
                            )
                            logger.info(
                                "Пример карточки: id=%s, name=%s, salePriceU=%s, priceU=%s, price_rub=%s",
                                preview.get("id"),
                                preview.get("name"),
                                sale_price_u,
                                price_u,
                                price_rub_preview,
                            )
                            return products, label, url, dest, spp

                        if response.status_code == 200:
                            break

        html_url = url_html_search(query, page)
        html_headers = {
            "User-Agent": self._headers.get("User-Agent", HEADERS["User-Agent"]),
            "Referer": "https://www.wildberries.ru/",
        }
        try:
            response = await client.get(html_url, headers=html_headers, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            logger.error("WB HTML fallback error: %s", exc)
            return [], "empty", html_url, None, None

        ids = [int(match) for match in NM_RE.findall(response.text)]
        logger.info(
            "WB HTML fallback: %s -> статус=%s nmIds=%s",
            html_url,
            response.status_code,
            len(ids),
        )
        if not ids:
            return [], "empty", html_url, None, None

        fake_products = [{"id": nm_id} for nm_id in ids]
        return fake_products, "html", html_url, None, None

    async def _enrich_products_from_details(
        self, items: list[dict[str, Any]], *, timeout: float
    ) -> None:
        missing_ids: list[int] = []
        for item in items:
            product_id = item.get("id")
            if product_id is None:
                continue

            try:
                product_id_int = int(product_id)
            except (TypeError, ValueError):
                continue

            needs_detail = False
            if not item.get("salePriceU") and not item.get("priceU"):
                needs_detail = True
            if not item.get("name") or not item.get("brand"):
                needs_detail = True
            if needs_detail:
                missing_ids.append(product_id_int)

        if not missing_ids:
            return

        detail_map = await self._fetch_details(missing_ids, timeout=timeout)
        for item in items:
            product_id = item.get("id")
            if product_id is None:
                continue
            try:
                product_id_int = int(product_id)
            except (TypeError, ValueError):
                continue

            detail = detail_map.get(product_id_int)
            if not detail:
                continue

            for key in ("name", "brand", "salePriceU", "priceU", "sale"):
                if not item.get(key) and detail.get(key) not in (None, ""):
                    item[key] = detail.get(key)

            if not item.get("reviewRating") and detail.get("reviewRating") is not None:
                item["reviewRating"] = detail.get("reviewRating")
            if not item.get("feedbacks") and detail.get("feedbacks") is not None:
                item["feedbacks"] = detail.get("feedbacks")
            if not item.get("supplierName") and detail.get("supplierName"):
                item["supplierName"] = detail.get("supplierName")
            if not item.get("supplierRating") and detail.get("supplierRating") is not None:
                item["supplierRating"] = detail.get("supplierRating")
            if not item.get("supplierOrders") and detail.get("supplierOrders") is not None:
                item["supplierOrders"] = detail.get("supplierOrders")

            extended = detail.get("extended")
            if isinstance(extended, Mapping):
                if not item.get("salePriceU") and extended.get("clientPriceU"):
                    item["salePriceU"] = extended.get("clientPriceU")
                if not item.get("priceU") and extended.get("promoPriceU"):
                    item["priceU"] = extended.get("promoPriceU")

    async def _fetch_details(
        self, product_ids: Iterable[int], *, timeout: float
    ) -> Mapping[int, dict[str, Any]]:
        ids = [str(pid) for pid in product_ids]
        if not ids:
            return {}

        params = {
            "appType": 1,
            "curr": "rub",
            "dest": -1257786,
            "nm": ",".join(ids),
        }

        async with httpx.AsyncClient(
            timeout=timeout,
            headers=self._headers,
            follow_redirects=True,
            http2=self._http2_enabled,
        ) as client:
            response = await client.get(DETAIL_API_URL, params=params)
            response.raise_for_status()
            payload = response.json()

        details = payload.get("data", {}).get("products", [])
        return {int(item.get("id")): item for item in details if item.get("id") is not None}

    def _build_product(
        self,
        base: Mapping[str, Any],
        detail: Mapping[str, Any],
        *,
        features: Sequence[str] | None = None,
        stock: int | None = None,
        registration: str | None = None,
        discount: float | None = None,
    ) -> Product:
        product_id = int(base.get("id", detail.get("id", 0)))
        sale_price = self._price_to_rub(base.get("salePriceU"))
        wallet_price = self._price_to_rub(
            detail.get("extended", {}).get("clientPriceU")
            or base.get("salePriceU")
        )
        best_buyout_price = self._price_to_rub(
            detail.get("extended", {}).get("promoPriceU")
            or detail.get("priceU")
            or base.get("priceU")
        )

        profit_rub = None
        profit_percent = None
        if wallet_price is not None and best_buyout_price is not None:
            profit_rub = round(best_buyout_price - wallet_price, 2)
            if wallet_price > 0:
                profit_percent = round((profit_rub / wallet_price) * 100, 2)

        rating = self._safe_float(detail.get("reviewRating") or base.get("reviewRating"))
        reviews = self._safe_int(detail.get("feedbacks") or base.get("feedbacks"))

        if features is None:
            features = self._extract_features(detail)
        if stock is None:
            stock = self._extract_stock(detail)

        seller_name = str(
            detail.get("supplierName")
            or base.get("supplierName")
            or detail.get("supplier")
            or "Неизвестный продавец"
        )
        seller_rating = self._safe_float(
            detail.get("supplierRating") or base.get("supplierRating")
        )
        seller_orders = self._safe_int(
            detail.get("supplierOrders")
            or detail.get("supplierGoodsCount")
            or base.get("supplierOrders")
            or base.get("supplierGoodsCount")
        )
        if registration is None:
            registration = self._extract_registration(detail)

        return Product(
            id=product_id,
            name=str(base.get("name", detail.get("name", ""))),
            brand=str(base.get("brand", detail.get("brand", ""))),
            price=sale_price,
            wallet_price=wallet_price,
            best_buyout_price=best_buyout_price,
            discount=discount,
            profit_rub=profit_rub,
            profit_percent=profit_percent,
            rating=rating,
            reviews=reviews,
            features=features,
            stock=stock,
            seller_name=seller_name,
            seller_rating=seller_rating,
            seller_orders=seller_orders,
            seller_registration=registration,
            url=f"https://www.wildberries.ru/catalog/{product_id}/detail.aspx",
            image_url=self._extract_photo(detail, product_id),
        )

    @staticmethod
    def _price_units_to_rub(value: Any) -> int | None:
        try:
            units = int(value)
        except (TypeError, ValueError):
            return None
        return units // 100

    def _compute_discount_percent(self, item: Mapping[str, Any]) -> float | None:
        sale_units = self._safe_int(item.get("salePriceU"))
        base_units = self._safe_int(item.get("priceU"))

        if base_units and sale_units is not None and base_units > 0:
            discount = (base_units - sale_units) / base_units * 100
            return round(discount, 2)

        sale_percent = self._safe_float(item.get("sale"))
        if sale_percent is not None:
            return float(sale_percent)

        return None

    def _apply_scores(self, candidates: list[dict[str, Any]]) -> None:
        prices = [c.get("price_rub") for c in candidates if c.get("price_rub") is not None]
        discounts = [c.get("discount") for c in candidates if c.get("discount") is not None]
        ratings = [c.get("rating") for c in candidates if c.get("rating") is not None]
        feedbacks_list = [
            c.get("feedbacks") for c in candidates if c.get("feedbacks") is not None
        ]

        def normalize(value: float | int | None, values: list[float | int]) -> float | None:
            if value is None or not values:
                return None
            min_value = float(min(values))
            max_value = float(max(values))
            if max_value == min_value:
                return 0.5
            return (float(value) - min_value) / (max_value - min_value)

        for candidate in candidates:
            price_norm = normalize(candidate.get("price_rub"), prices)
            discount_norm = normalize(candidate.get("discount"), discounts)
            rating_norm = normalize(candidate.get("rating"), ratings)
            feedbacks_norm = normalize(candidate.get("feedbacks"), feedbacks_list)

            weighted_values: list[tuple[float, float]] = []
            if price_norm is not None:
                weighted_values.append((0.35, 1 - price_norm))
            if discount_norm is not None:
                weighted_values.append((0.25, discount_norm))
            if rating_norm is not None:
                weighted_values.append((0.25, rating_norm))
            if feedbacks_norm is not None:
                weighted_values.append((0.15, feedbacks_norm))

            if not weighted_values:
                candidate["score"] = None
                continue

            total_weight = sum(weight for weight, _ in weighted_values)
            score = sum(weight * value for weight, value in weighted_values) / total_weight
            candidate["score"] = round(score, 4)

    @staticmethod
    def _price_to_rub(value: Any) -> float | None:
        if value is None:
            return None
        try:
            price = float(value) / 100
        except (TypeError, ValueError):
            return None
        return round(price, 2)

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        try:
            if value is None:
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_features(detail: Mapping[str, Any]) -> List[str]:
        options = detail.get("options") or detail.get("characteristics") or []
        features: List[str] = []
        for option in options:
            name = option.get("name") if isinstance(option, Mapping) else None
            value = option.get("value") if isinstance(option, Mapping) else None
            if name and value:
                features.append(f"{name}: {value}")
            elif value:
                features.append(str(value))
        return features[:5]

    @staticmethod
    def _extract_stock(detail: Mapping[str, Any]) -> int | None:
        sizes = detail.get("sizes")
        if not isinstance(sizes, list):
            return None

        total = 0
        for size in sizes:
            stocks = size.get("stocks") if isinstance(size, Mapping) else None
            if not isinstance(stocks, list):
                continue
            for stock in stocks:
                qty = stock.get("qty") if isinstance(stock, Mapping) else None
                if isinstance(qty, (int, float)):
                    total += int(qty)
        return total if total > 0 else None

    @staticmethod
    def _extract_registration(detail: Mapping[str, Any]) -> str | None:
        supplier_info = detail.get("supplierInfo")
        if isinstance(supplier_info, Mapping):
            date = supplier_info.get("registrationDate") or supplier_info.get("regDate")
            if date:
                return str(date)
        return None

    @staticmethod
    def _extract_photo(detail: Mapping[str, Any], product_id: int) -> str | None:
        photos = detail.get("photos")
        if isinstance(photos, list):
            for photo in photos:
                if not isinstance(photo, Mapping):
                    continue
                for key in ("c516x688", "big", "large", "small"):
                    path = photo.get(key)
                    if path:
                        if path.startswith("http"):
                            return str(path)
                        return f"https://images.wbstatic.net/{path}"

        return build_image_url(product_id)

    @staticmethod
    def _build_image_url(product_id: int) -> str:
        return build_image_url(product_id)
