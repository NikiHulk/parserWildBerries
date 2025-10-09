from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Sequence

import httpx

API_URL = "https://search.wb.ru/exactmatch/ru/common/v4/search"
DETAIL_API_URL = "https://card.wb.ru/cards/detail"

DEFAULT_SEARCH_PARAMS = {
    "appType": 1,
    "curr": "rub",
    "dest": -1257786,
    "regions": (
        "80,64,38,4,115,83,33,68,70,86,75,30,40,48,69,22,66,31,1,114"
    ),
    "spp": 0,
}


@dataclass(slots=True)
class Product:
    id: int
    name: str
    brand: str
    price: float | None
    wallet_price: float | None
    best_buyout_price: float | None
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
    photo_url: str | None


class WildberriesClient:
    """Client for fetching products from Wildberries search API."""

    def __init__(self, timeout: float = 10.0, *, user_agent: str | None = None) -> None:
        self._timeout = timeout
        self._headers = {
            "User-Agent": user_agent
            or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }

    async def search(
        self,
        query: str,
        min_price: float = 0.0,
        limit: int = 10,
        exclude_words: Iterable[str] | None = None,
    ) -> List[Product]:
        if limit <= 0:
            return []

        normalized_excludes = [word.strip().lower() for word in (exclude_words or []) if word.strip()]
        max_candidates = max(limit * 5, limit or 1)
        max_pages = 10

        candidates: list[dict[str, Any]] = []
        min_price_units = int(min_price * 100)

        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
            page = 1
            while len(candidates) < max_candidates and page <= max_pages:
                params = {
                    "query": query,
                    "limit": 100,
                    "resultset": "catalog",
                    "sort": "rate",
                    "page": page,
                }
                params.update(DEFAULT_SEARCH_PARAMS)

                response = await client.get(API_URL, params=params)
                response.raise_for_status()
                payload = response.json()

                products_data: Iterable[dict[str, Any]] = payload.get("data", {}).get("products", [])
                if not products_data:
                    break

                for item in products_data:
                    if not self._passes_basic_filters(item, min_price_units, normalized_excludes):
                        continue
                    candidates.append(item)
                    if len(candidates) >= max_candidates:
                        break

                page += 1

        products: List[Product] = []
        detail_map = await self._fetch_details(int(item["id"]) for item in candidates)

        for item in candidates:
            product_id = int(item["id"])
            detail = detail_map.get(product_id, {})
            features = self._extract_features(detail)

            if normalized_excludes and self._contains_in_detail(
                normalized_excludes, item, detail, features
            ):
                continue

            stock = self._extract_stock(detail)
            registration = self._extract_registration(detail)
            products.append(
                self._build_product(
                    item,
                    detail,
                    features=features,
                    stock=stock,
                    registration=registration,
                )
            )

            if len(products) >= limit:
                break

        return products

    async def _fetch_details(self, product_ids: Iterable[int]) -> Mapping[int, dict[str, Any]]:
        ids = [str(pid) for pid in product_ids]
        if not ids:
            return {}

        params = {
            "appType": 1,
            "curr": "rub",
            "dest": -1257786,
            "nm": ",".join(ids),
        }

        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
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
            photo_url=self._extract_photo(detail, product_id),
        )

    @staticmethod
    def _passes_basic_filters(
        item: Mapping[str, Any],
        min_price_units: int,
        excludes: list[str],
    ) -> bool:
        sale_price = WildberriesClient._safe_int(item.get("salePriceU"))
        if sale_price is None:
            sale_price = WildberriesClient._safe_int(item.get("priceU"))
        if sale_price is None or sale_price < min_price_units:
            return False

        if item.get("id") is None:
            return False

        if excludes:
            name_text = str(item.get("name", "")).lower()
            brand_text = str(item.get("brand", "")).lower()
            haystack = f"{name_text} {brand_text}".strip()
            if WildberriesClient._contains_words(excludes, [haystack]):
                return False

        return True

    @staticmethod
    def _contains_in_detail(
        excludes: list[str],
        base: Mapping[str, Any],
        detail: Mapping[str, Any],
        features: Sequence[str],
    ) -> bool:
        texts: list[str] = [
            str(base.get("name", "")),
            str(base.get("brand", "")),
            str(detail.get("name", "")),
            str(detail.get("brand", "")),
            str(detail.get("description", "")),
            str(detail.get("supplierName", "")),
            str(detail.get("supplier", "")),
            str(detail.get("subjectName", "")),
            str(detail.get("subj_root_name", "")),
            str(detail.get("root", "")),
        ]

        extended = detail.get("extended")
        if isinstance(extended, Mapping):
            texts.append(str(extended.get("promoTextCard", "")))

        options = detail.get("options") or detail.get("characteristics")
        if isinstance(options, list):
            for option in options:
                if isinstance(option, Mapping):
                    texts.append(str(option.get("name", "")))
                    texts.append(str(option.get("value", "")))
                else:
                    texts.append(str(option))

        tags = detail.get("tags")
        if isinstance(tags, list):
            for tag in tags:
                texts.append(str(tag))

        texts.extend(str(feature) for feature in features)

        return WildberriesClient._contains_words(excludes, texts)

    @staticmethod
    def _contains_words(words: Sequence[str], texts: Sequence[str]) -> bool:
        normalized_text = " ".join(str(text).lower() for text in texts if text)
        return any(word in normalized_text for word in words)

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

        return f"https://images.wbstatic.net/big/new/{product_id // 1000}/{product_id}-1.jpg"
