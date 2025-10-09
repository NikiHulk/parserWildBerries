from __future__ import annotations

import asyncio
import importlib.util
import logging
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


def wb_catalog_url(query: str, page: int = 1, limit: int = 100) -> str:
    """Возвращает корректный JSON-эндпоинт каталога без .aspx."""

    return (
        "https://catalog.wb.ru/catalog/0/search"
        f"?appType=1&curr=rub&dest=-1257786&spp=30"
        f"&page={page}&limit={limit}&query={quote_plus(query)}"
    )


def wb_exactmatch_url(query: str, page: int = 1, limit: int = 100) -> str:
    return (
        "https://search.wb.ru/exactmatch/ru/common/v4/search"
        f"?appType=1&curr=rub&dest=-1257786&spp=30&resultset=catalog"
        f"&page={page}&limit={limit}&query={quote_plus(query)}"
    )


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
                _, _, products_list = await self._fetch_page(
                    client,
                    query=query,
                    page=page,
                    limit=100,
                    timeout=effective_timeout,
                )

                products_list = list(products_list or [])
                total_received += len(products_list)

                if not products_list:
                    break

                page_candidates: list[dict[str, Any]] = []
                for item in products_list:
                    product_id = item.get("id")
                    if product_id is None:
                        continue

                    price_units = item.get("salePriceU") or item.get("priceU") or 0
                    price_rub = self._price_units_to_rub(price_units) or 0

                    if (
                        min_price_rub is not None
                        and price_rub < min_price_rub
                    ):
                        filtered_by_price += 1
                        continue

                    if (
                        max_price_rub is not None
                        and price_rub > max_price_rub
                    ):
                        filtered_by_price += 1
                        continue

                    if normalized_banned:
                        haystack = (
                            f"{item.get('name', '')} {item.get('brand', '')}"
                        ).lower()
                        if any(word in haystack for word in normalized_banned):
                            filtered_by_banned += 1
                            continue

                    rating = self._safe_float(item.get("reviewRating"))
                    if (
                        self._min_rating is not None
                        and rating is not None
                        and rating < self._min_rating
                    ):
                        filtered_by_rating += 1
                        continue

                    feedbacks = self._safe_int(item.get("feedbacks"))
                    if (
                        self._min_feedbacks is not None
                        and feedbacks is not None
                        and feedbacks < self._min_feedbacks
                    ):
                        filtered_by_feedbacks += 1
                        continue

                    discount_percent = self._compute_discount_percent(item)
                    if (
                        self._min_discount is not None
                        and discount_percent is not None
                        and discount_percent < self._min_discount
                    ):
                        filtered_by_discount += 1
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
                logger.info(
                    "После фильтра: %s товаров на странице (суммарно %s)",
                    len(page_candidates),
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
    ) -> tuple[str, int | None, Sequence[Mapping[str, Any]]]:
        catalog_url = wb_catalog_url(query, page=page, limit=limit)
        response = await self._request_with_retry(
            client,
            catalog_url,
            timeout=timeout,
        )
        products = self._parse_products(response, catalog_url, "catalog")
        preview = products[0] if products else None
        self._log_page("catalog", catalog_url, response, products, preview)

        if response.status_code == 200 and products:
            return "catalog", response.status_code, products

        exact_url = wb_exactmatch_url(query, page=page, limit=limit)
        fallback_response = await self._request_with_retry(
            client,
            exact_url,
            timeout=timeout,
        )
        fallback_products = self._parse_products(
            fallback_response,
            exact_url,
            "exactmatch",
        )
        fallback_preview = fallback_products[0] if fallback_products else None
        self._log_page(
            "exactmatch",
            exact_url,
            fallback_response,
            fallback_products,
            fallback_preview,
        )

        if fallback_response.status_code == 200 and fallback_products:
            return "exactmatch", fallback_response.status_code, fallback_products

        status = (
            fallback_response.status_code
            if fallback_response is not None
            else response.status_code
        )
        logger.info(
            "WB поиск (empty): %s -> статус %s, товаров: %s",
            exact_url,
            status,
            len(fallback_products or []),
        )
        return "empty", status, fallback_products

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        timeout: float,
        attempts: int = 3,
    ) -> httpx.Response:
        last_exception: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                return await client.get(url, timeout=timeout)
            except (httpx.ReadTimeout, httpx.ConnectError) as exc:
                last_exception = exc
                if attempt == attempts:
                    raise
                delay = 0.3 * attempt
                logger.warning(
                    "Ошибка сети %s при обращении к %s (попытка %s/%s). Повтор через %.1f с",
                    exc.__class__.__name__,
                    url,
                    attempt,
                    attempts,
                    delay,
                )
                await asyncio.sleep(delay)

        if last_exception is not None:
            raise last_exception

        raise RuntimeError("Не удалось выполнить запрос к Wildberries")

    def _parse_products(
        self,
        response: httpx.Response,
        url: str,
        source: str,
    ) -> list[Mapping[str, Any]]:
        """Пытаемся разобрать JSON-ответ Wildberries с защитой от HTML-ошибок."""

        try:
            payload = response.json()
        except Exception:
            logger.error(
                "WB вернул не-JSON (%s %s). Статус: %s. Тело (первые 200 символов): %r",
                source,
                url,
                response.status_code,
                (response.text or "")[:200],
            )
            return []

        data = payload.get("data")
        if not isinstance(data, Mapping):
            return []

        products_data = data.get("products")
        if isinstance(products_data, list):
            return [item for item in products_data if isinstance(item, Mapping)]
        if isinstance(products_data, Iterable):
            return [item for item in products_data if isinstance(item, Mapping)]
        return []

    def _log_page(
        self,
        source: str,
        url: str,
        response: httpx.Response,
        products: Sequence[Mapping[str, Any]],
        preview: Mapping[str, Any] | None,
    ) -> None:
        status = response.status_code if response is not None else None
        logger.info(
            "WB поиск (%s): %s -> статус %s, товаров: %s",
            source,
            url,
            status,
            len(products),
        )
        if preview:
            sale_price_u = preview.get("salePriceU")
            price_u = preview.get("priceU")
            price_rub_preview = self._price_units_to_rub(sale_price_u or price_u)
            logger.info(
                "Пример карточки: id=%s, name=%s, salePriceU=%s, priceU=%s, price_rub=%s",
                preview.get("id"),
                preview.get("name"),
                sale_price_u,
                price_u,
                price_rub_preview,
            )

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
