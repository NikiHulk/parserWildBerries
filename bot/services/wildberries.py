from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List

import httpx

API_URL = "https://search.wb.ru/exactmatch/ru/common/v4/search"


@dataclass(slots=True)
class Product:
    id: int
    name: str
    brand: str
    price: float
    url: str


class WildberriesClient:
    """Client for fetching products from Wildberries search API."""

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    async def search(self, query: str, min_price: float = 0.0, limit: int = 10) -> List[Product]:
        params = {
            "query": query,
            "limit": limit,
            "resultset": "catalog",
            "sort": "rate",
            "page": 1,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(API_URL, params=params)
            response.raise_for_status()
            payload = response.json()

        products_data: Iterable[dict[str, Any]] = payload.get("data", {}).get("products", [])
        products: List[Product] = []
        min_price_units = int(min_price * 100)

        for item in products_data:
            sale_price = item.get("salePriceU")
            if sale_price is None or sale_price < min_price_units:
                continue

            product_id = item.get("id")
            if product_id is None:
                continue

            products.append(
                Product(
                    id=int(product_id),
                    name=str(item.get("name", "")),
                    brand=str(item.get("brand", "")),
                    price=sale_price / 100,
                    url=f"https://www.wildberries.ru/catalog/{product_id}/detail.aspx",
                )
            )

        return products[:limit]
