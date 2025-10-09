from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import random
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger(__name__)

DETAIL_API_URL = "https://card.wb.ru/cards/v1/detail"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru,en;q=0.9",
    "Referer": "https://www.wildberries.ru/",
    "Connection": "keep-alive",
    "Accept-Encoding": "gzip, deflate, br",
}

PLAYWRIGHT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

COOKIE_TTL_SECONDS = 6 * 60 * 60
COOKIE_FILE = Path("data/wb_cookies.json")

START_LIMIT = 20
MAX_CATALOG_ATTEMPTS = 2
PAGE_DELAY_RANGE = (0.30, 0.70)
DETAIL_BATCH = 100
MAX_HTML_IDS = 120

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
]

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


@dataclass(slots=True)
class PageFetchMeta:
    products: list[dict[str, Any]]
    source: str
    url: str
    dest: int | None
    spp: int | None
    status: int | str | None
    cache: str
    had_429: bool
    limit: int
    timing_ms: float
    html_ids: int | None = None
    html_enriched: int | None = None


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
        page_delay_ms: float | None = None,
        force_html_first: bool = False,
    ) -> None:
        self._timeout = timeout
        self._headers = dict(HEADERS)
        if user_agent:
            self._headers["User-Agent"] = user_agent
        self._user_agent_locked = bool(user_agent)
        self._http2_enabled = self._detect_http2_support()
        self._min_rating = min_rating
        self._min_feedbacks = min_feedbacks
        self._min_discount = min_discount
        self._cache: dict[
            tuple[Any, ...],
            tuple[float, int | None, str, str, tuple[dict[str, Any], ...]],
        ] = {}
        self.force_html_first = bool(force_html_first)
        self._cache_ttl = 45.0
        self._last_page_logs: list[dict[str, Any]] = []
        self._page_delay_override = (
            float(page_delay_ms) / 1000 if page_delay_ms is not None else None
        )
        self._page_delay_range = PAGE_DELAY_RANGE
        self._cookie_file = COOKIE_FILE
        self._cookie_ttl = COOKIE_TTL_SECONDS
        self._cookie_cache: list[dict[str, Any]] | None = None
        self._cookie_cache_ts: float | None = None
        self._cookie_lock = asyncio.Lock()

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
        self._last_page_logs = []

        min_price_rub = self._normalize_price(min_price)
        max_price_rub = self._normalize_price(max_price)

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
        consecutive_empty_pages = 0
        rate_limit_hits = 0

        effective_timeout = float(timeout or self._timeout)
        current_limit = START_LIMIT

        session_headers = dict(self._headers)
        if not self._user_agent_locked:
            session_headers["User-Agent"] = random.choice(USER_AGENTS)
        session_headers.setdefault("Accept-Encoding", HEADERS["Accept-Encoding"])

        async with httpx.AsyncClient(
            timeout=effective_timeout,
            headers=session_headers,
            follow_redirects=True,
            http2=self._http2_enabled,
        ) as client:
            page = 1

            while len(filtered_candidates) < max_results:
                page_meta = await self._fetch_page(
                    client,
                    headers=session_headers,
                    query=query,
                    page=page,
                    limit=current_limit,
                    timeout=effective_timeout,
                    force_html_first=self.force_html_first,
                )

                raw_products = page_meta.products or []
                products_list: list[dict[str, Any]] = [
                    item if isinstance(item, dict) else dict(item)
                    for item in raw_products
                ]
                total_received += len(products_list)

                before_count = len(products_list)
                page_price_filtered = 0
                page_banned_filtered = 0
                page_rating_filtered = 0
                page_feedback_filtered = 0
                page_discount_filtered = 0

                if not products_list:
                    consecutive_empty_pages += 1
                    self._log_page_fetch(
                        page=page,
                        page_meta=page_meta,
                        before_count=0,
                        after_price=0,
                        after_banned=0,
                        after_quality=0,
                        total=len(filtered_candidates),
                    )

                    if page_meta.had_429:
                        rate_limit_hits += 1
                        current_limit = self._adjust_limit_on_rate_limit(
                            current_limit,
                            rate_limit_hits,
                        )

                    if consecutive_empty_pages >= 2:
                        break

                    if total_received >= max(100, max_results * 2):
                        break

                    page += 1
                    await self._sleep_between_pages()
                    continue

                consecutive_empty_pages = 0

                await self._enrich_products_from_details(
                    products_list,
                    timeout=effective_timeout,
                    headers=session_headers,
                )

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

                top_items: list[dict[str, Any]] = []
                for candidate in page_candidates[:3]:
                    candidate_item = candidate.get("item", {})
                    try:
                        candidate_id = int(candidate_item.get("id"))
                    except (TypeError, ValueError):
                        continue
                    top_items.append(
                        {
                            "id": candidate_id,
                            "price": candidate.get("price_rub"),
                            "url": f"https://www.wildberries.ru/catalog/{candidate_id}/detail.aspx",
                        }
                    )

                self._log_page_fetch(
                    page=page,
                    page_meta=page_meta,
                    before_count=before_count,
                    after_price=after_price_count,
                    after_banned=after_banned_count,
                    after_quality=after_quality_count,
                    total=len(filtered_candidates),
                    top_items=top_items,
                )

                if page_meta.had_429:
                    rate_limit_hits += 1
                    current_limit = self._adjust_limit_on_rate_limit(
                        current_limit,
                        rate_limit_hits,
                    )

                if len(filtered_candidates) >= max_results:
                    break

                if total_received >= max(100, max_results * 2):
                    break

                page += 1
                await self._sleep_between_pages()

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
            headers=session_headers,
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

    @staticmethod
    def _normalize_price(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return None
        if candidate <= 0:
            return None
        return candidate

    def _adjust_limit_on_rate_limit(self, current_limit: int, rate_limit_hits: int) -> int:
        if rate_limit_hits == 1 and current_limit > 30:
            logger.warning(
                "WB ответил 429: уменьшаем лимит страницы до %s",
                30,
            )
            return 30
        if rate_limit_hits >= 2 and current_limit > 20:
            logger.warning(
                "Повторный 429: уменьшаем лимит страницы до %s",
                20,
            )
            return 20
        return current_limit

    async def _sleep_between_pages(self) -> None:
        delay = (
            self._page_delay_override
            if self._page_delay_override is not None
            else random.uniform(*self._page_delay_range)
        )
        if delay and delay > 0:
            await asyncio.sleep(delay)

    async def _get_html_cookies(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        async with self._cookie_lock:
            now = time.time()
            if not force_refresh:
                if (
                    self._cookie_cache is not None
                    and self._cookie_cache_ts is not None
                    and now - self._cookie_cache_ts < self._cookie_ttl
                ):
                    logger.info(
                        "[WB/Playwright] Использую сохранённые cookies (in-memory %s)",
                        len(self._cookie_cache),
                    )
                    return list(self._cookie_cache)

                cookies, ts = await self._load_cookies_from_disk()
                if cookies is not None and ts is not None and now - ts < self._cookie_ttl:
                    self._cookie_cache = cookies
                    self._cookie_cache_ts = ts
                    logger.info(
                        "[WB/Playwright] Использую сохранённые cookies (file %s)", len(cookies)
                    )
                    return list(cookies)

            logger.info("[WB/Playwright] Обновление cookies из браузера")
            cookies = await self._warm_up_browser_session()
            self._cookie_cache = cookies
            self._cookie_cache_ts = time.time()
            await self._save_cookies_to_disk(cookies)
            return list(cookies)

    async def _load_cookies_from_disk(self) -> tuple[list[dict[str, Any]] | None, float | None]:
        def _load() -> tuple[list[dict[str, Any]] | None, float | None]:
            if not self._cookie_file.exists():
                return None, None
            try:
                stat = self._cookie_file.stat()
                with self._cookie_file.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                logger.warning("[WB/Playwright] Не удалось прочитать cookies: %s", exc)
                return None, None
            cookies = self._filter_cookies(data)
            names = [c.get("name") for c in cookies if c.get("name")]
            logger.info(
                "[WB/Playwright] Файл cookies содержит %s записей: %s",
                len(cookies),
                ", ".join(names),
            )
            return cookies, stat.st_mtime

        return await asyncio.to_thread(_load)

    async def _save_cookies_to_disk(self, cookies: Sequence[Mapping[str, Any]]) -> None:
        def _save() -> None:
            try:
                self._cookie_file.parent.mkdir(parents=True, exist_ok=True)
                with self._cookie_file.open("w", encoding="utf-8") as fh:
                    json.dump(list(cookies), fh, ensure_ascii=False, indent=2)
            except OSError as exc:
                logger.warning("[WB/Playwright] Не удалось сохранить cookies: %s", exc)

        await asyncio.to_thread(_save)
        names = [c.get("name") for c in cookies if c.get("name")]
        logger.info(
            "[WB/Playwright] Получены новые cookies (%s): %s",
            len(cookies),
            ", ".join(names),
        )

    async def _warm_up_browser_session(self) -> list[dict[str, Any]]:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            logger.error(
                "Playwright не установлен. Установите 'playwright' и выполните 'playwright install chromium'."
            )
            raise

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                locale="ru-RU",
                user_agent=PLAYWRIGHT_USER_AGENT,
            )
            page = await context.new_page()
            await page.goto("https://www.wildberries.ru/", wait_until="networkidle")
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(1.0)
            cookies = await context.cookies()
            await context.close()
            await browser.close()

        filtered = self._filter_cookies(cookies)
        return filtered

    def _filter_cookies(self, cookies: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        for cookie in cookies:
            name = cookie.get("name")
            value = cookie.get("value")
            domain = cookie.get("domain") or ""
            if not name or value is None:
                continue
            if "wildberries.ru" not in domain:
                continue
            filtered.append(
                {
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": cookie.get("path", "/"),
                    "expires": cookie.get("expires"),
                    "secure": cookie.get("secure", False),
                    "httpOnly": cookie.get("httpOnly", False),
                }
            )
        return filtered

    def _apply_cookies_to_client(
        self, client: httpx.AsyncClient, cookies: Sequence[Mapping[str, Any]]
    ) -> None:
        jar = httpx.Cookies()
        for cookie in cookies:
            name = cookie.get("name")
            value = cookie.get("value")
            if not name or value is None:
                continue
            domain = cookie.get("domain") or ".wildberries.ru"
            path = cookie.get("path") or "/"
            jar.set(name, value, domain=domain, path=path)
        client.cookies.clear()
        client.cookies.update(jar)

    @property
    def last_page_logs(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self._last_page_logs]

    def _log_page_fetch(
        self,
        *,
        page: int,
        page_meta: PageFetchMeta,
        before_count: int,
        after_price: int,
        after_banned: int,
        after_quality: int,
        total: int,
        top_items: list[dict[str, Any]] | None = None,
    ) -> None:
        summary = {
            "page": page,
            "source": page_meta.source,
            "status": page_meta.status,
            "products": before_count,
            "limit": page_meta.limit,
            "dest": page_meta.dest,
            "spp": page_meta.spp,
            "cache": page_meta.cache,
            "timing_ms": page_meta.timing_ms,
            "before": before_count,
            "after_price": after_price,
            "after_banned": after_banned,
            "after_quality": after_quality,
            "total_so_far": total,
            "top_items": list(top_items or []),
            "html_ids": page_meta.html_ids,
            "html_enriched": page_meta.html_enriched,
        }
        self._last_page_logs.append(summary)

        extra_parts: list[str] = []
        if page_meta.source == "html_detail":
            if page_meta.html_ids is not None:
                extra_parts.append(f"ids={page_meta.html_ids}")
            if page_meta.html_enriched is not None:
                extra_parts.append(f"enriched={page_meta.html_enriched}")
        extra_suffix = f" {' '.join(extra_parts)}" if extra_parts else ""

        timing_int = int(page_meta.timing_ms) if page_meta.timing_ms else 0
        logger.info(
            "WB page=%s source=%s status=%s products=%s limit=%s dest=%s spp=%s "
            "cache=%s timing=%sms%s",
            page,
            page_meta.source,
            page_meta.status,
            before_count,
            page_meta.limit,
            page_meta.dest,
            page_meta.spp,
            page_meta.cache,
            timing_int,
            extra_suffix,
        )
        logger.info(
            "WB filter page=%s before=%s after_price=%s after_banned=%s after_quality=%s total=%s",
            page,
            before_count,
            after_price,
            after_banned,
            after_quality,
            total,
        )

    def _cache_key(
        self, query: str, page: int, limit: int, dest: int, spp: int
    ) -> tuple[Any, ...]:
        return (query.lower(), page, limit, dest, spp)

    def _cache_get(
        self, key: tuple[Any, ...]
    ) -> tuple[int | None, str, str, list[dict[str, Any]]] | None:
        entry = self._cache.get(key)
        if not entry:
            return None
        timestamp, status, label, url, payload = entry
        if time.monotonic() - timestamp > self._cache_ttl:
            self._cache.pop(key, None)
            return None
        return status, label, url, [dict(item) for item in payload]

    def _cache_set(
        self,
        key: tuple[Any, ...],
        *,
        status: int | None,
        label: str,
        url: str,
        payload: list[dict[str, Any]],
    ) -> None:
        self._cache[key] = (
            time.monotonic(),
            status,
            label,
            url,
            tuple(dict(item) for item in payload),
        )

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        *,
        headers: Mapping[str, str],
        query: str,
        page: int,
        limit: int,
        timeout: float,
        force_html_first: bool,
    ) -> PageFetchMeta:
        start_time = time.monotonic()
        encountered_429 = False

        if force_html_first:
            return await self._html_fallback(
                client,
                headers=headers,
                query=query,
                page=page,
                limit=limit,
                timeout=timeout,
                encountered_429=False,
                start_time=start_time,
            )

        attempts_made = 0

        for dest in DESTS:
            for spp in SPPS:
                if attempts_made >= MAX_CATALOG_ATTEMPTS:
                    break

                key = self._cache_key(query, page, limit, dest, spp)
                cached = self._cache_get(key)
                if cached:
                    status, label, cached_url, payload = cached
                    return PageFetchMeta(
                        products=payload,
                        source=label,
                        url=cached_url,
                        dest=dest,
                        spp=spp,
                        status=status,
                        cache="hit",
                        had_429=False,
                        limit=limit,
                        timing_ms=(time.monotonic() - start_time) * 1000,
                    )

                # Первая попытка каталога
                url_main = url_catalog(query, page, limit, dest, spp)
                response, saw_429 = await self._request_with_backoff(
                    client,
                    "GET",
                    url_main,
                    headers=headers,
                    timeout=timeout,
                    max_retries=1,
                )
                attempts_made += 1
                status_main = response.status_code if response else None
                encountered_429 = encountered_429 or saw_429 or status_main == 429
                products_main: list[dict[str, Any]] = []
                if response is not None:
                    products_main = parse_products_json(response, url_main, "catalog")

                if products_main:
                    self._cache_set(
                        key,
                        status=status_main,
                        label="catalog",
                        url=url_main,
                        payload=products_main,
                    )
                    return PageFetchMeta(
                        products=[dict(item) for item in products_main],
                        source="catalog",
                        url=url_main,
                        dest=dest,
                        spp=spp,
                        status=status_main,
                        cache="miss",
                        had_429=encountered_429,
                        limit=limit,
                        timing_ms=(time.monotonic() - start_time) * 1000,
                    )

                first_attempt = attempts_made == 1
                main_empty = not products_main
                main_error = status_main in {404, 429}
                if first_attempt and (main_empty or main_error):
                    return await self._html_fallback(
                        client,
                        headers=headers,
                        query=query,
                        page=page,
                        limit=limit,
                        timeout=timeout,
                        encountered_429=encountered_429,
                        start_time=start_time,
                    )

                if attempts_made >= MAX_CATALOG_ATTEMPTS:
                    break

                await asyncio.sleep(random.uniform(*PAGE_DELAY_RANGE))

                # Вторая попытка через альтернативный хост
                url_alt = url_catalog_alt(query, page, limit, dest, spp)
                response_alt, saw_429_alt = await self._request_with_backoff(
                    client,
                    "GET",
                    url_alt,
                    headers=headers,
                    timeout=timeout,
                    max_retries=1,
                )
                attempts_made += 1
                status_alt = response_alt.status_code if response_alt else None
                encountered_429 = encountered_429 or saw_429_alt or status_alt == 429
                products_alt: list[dict[str, Any]] = []
                if response_alt is not None:
                    products_alt = parse_products_json(response_alt, url_alt, "catalog_alt")

                if products_alt:
                    self._cache_set(
                        key,
                        status=status_alt,
                        label="catalog_alt",
                        url=url_alt,
                        payload=products_alt,
                    )
                    return PageFetchMeta(
                        products=[dict(item) for item in products_alt],
                        source="catalog_alt",
                        url=url_alt,
                        dest=dest,
                        spp=spp,
                        status=status_alt,
                        cache="miss",
                        had_429=encountered_429,
                        limit=limit,
                        timing_ms=(time.monotonic() - start_time) * 1000,
                    )

                if status_alt in {404, 429} or not products_alt:
                    return await self._html_fallback(
                        client,
                        headers=headers,
                        query=query,
                        page=page,
                        limit=limit,
                        timeout=timeout,
                        encountered_429=encountered_429,
                        start_time=start_time,
                    )

            if attempts_made >= MAX_CATALOG_ATTEMPTS:
                break

        return await self._html_fallback(
            client,
            headers=headers,
            query=query,
            page=page,
            limit=limit,
            timeout=timeout,
            encountered_429=encountered_429,
            start_time=start_time,
        )

    async def _html_fallback(
        self,
        client: httpx.AsyncClient,
        *,
        headers: Mapping[str, str],
        query: str,
        page: int,
        limit: int,
        timeout: float,
        encountered_429: bool,
        start_time: float,
    ) -> PageFetchMeta:
        html_url = url_html_search(query, page)
        html_headers = dict(headers)
        cookies = await self._get_html_cookies()
        if cookies:
            self._apply_cookies_to_client(client, cookies)

        response, saw_429 = await self._request_with_backoff(
            client,
            "GET",
            html_url,
            headers=html_headers,
            timeout=timeout,
        )
        if response is not None and response.status_code in {403, 498}:
            cookies = await self._get_html_cookies(force_refresh=True)
            if cookies:
                self._apply_cookies_to_client(client, cookies)
            response, saw_429 = await self._request_with_backoff(
                client,
                "GET",
                html_url,
                headers=html_headers,
                timeout=timeout,
            )
        encountered_429 = encountered_429 or saw_429
        status = response.status_code if response else None
        ids: list[int] = []
        if response is not None:
            ids = [int(match) for match in NM_RE.findall(response.text)][:MAX_HTML_IDS]

        if not ids:
            return PageFetchMeta(
                products=[],
                source="html_detail",
                url=html_url,
                dest=None,
                spp=None,
                status="html_empty",
                cache="miss",
                had_429=encountered_429,
                limit=limit,
                timing_ms=(time.monotonic() - start_time) * 1000,
                html_ids=0,
                html_enriched=0,
            )

        detail_map = await self._fetch_details(
            ids,
            timeout=timeout,
            headers=headers,
            client=client,
        )

        enriched: list[dict[str, Any]] = []
        enriched_count = 0
        for nm_id in ids:
            detail = detail_map.get(nm_id)
            if isinstance(detail, Mapping):
                detail_copy = dict(detail)
                detail_copy.setdefault("id", nm_id)
                price_units = (
                    detail_copy.get("salePriceU")
                    or detail_copy.get("priceU")
                    or detail_copy.get("extended", {}).get("clientPriceU")
                )
                price_rub = self._price_units_to_rub(price_units)
                if price_rub is not None:
                    detail_copy.setdefault("price_rub", price_rub)
                enriched.append(detail_copy)
                enriched_count += 1
            else:
                enriched.append({"id": nm_id})

        status_label = "html_ok/detail_ok" if enriched_count else "html_ok/detail_empty"

        return PageFetchMeta(
            products=enriched,
            source="html_detail",
            url=html_url,
            dest=None,
            spp=None,
            status=status_label,
            cache="miss",
            had_429=encountered_429,
            limit=limit,
            timing_ms=(time.monotonic() - start_time) * 1000,
            html_ids=len(ids),
            html_enriched=enriched_count,
        )

    async def _request_with_backoff(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float,
        max_retries: int = 6,
        base_delay: float = 0.5,
        max_delay: float = 8.0,
        params: Mapping[str, Any] | None = None,
    ) -> tuple[httpx.Response | None, bool]:
        saw_rate_limit = False
        last_response: httpx.Response | None = None
        for attempt in range(max_retries):
            try:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    timeout=timeout,
                    params=params,
                )
            except (httpx.ReadTimeout, httpx.ConnectError) as exc:
                sleep_time = min(
                    base_delay * (2**attempt) + random.uniform(0.1, 0.4),
                    max_delay,
                )
                logger.warning(
                    "WB запрос ошибка (%s %s) попытка %s/%s: %s, sleep=%.2fs",
                    method,
                    url,
                    attempt + 1,
                    max_retries,
                    exc,
                    sleep_time,
                )
                await asyncio.sleep(sleep_time)
                continue

            last_response = response
            status = response.status_code
            if status in {429, 403, 498, 502, 503, 504}:
                if status == 429:
                    saw_rate_limit = True
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        sleep_time = max(float(retry_after), 0.0)
                    except (TypeError, ValueError):
                        sleep_time = min(
                            base_delay * (2**attempt) + random.uniform(0.1, 0.4),
                            max_delay,
                        )
                else:
                    sleep_time = min(
                        base_delay * (2**attempt) + random.uniform(0.1, 0.4),
                        max_delay,
                    )
                logger.warning(
                    "WB статус %s (%s) попытка %s/%s, sleep=%.2fs",
                    status,
                    url,
                    attempt + 1,
                    max_retries,
                    sleep_time,
                )
                await asyncio.sleep(sleep_time)
                continue

            if 200 <= status < 300:
                return response, saw_rate_limit

            if attempt == 0:
                sleep_time = min(base_delay + random.uniform(0.1, 0.4), max_delay)
                logger.warning(
                    "WB неожиданный статус %s (%s), повтор через %.2fs",
                    status,
                    url,
                    sleep_time,
                )
                await asyncio.sleep(sleep_time)
                continue

            return response, saw_rate_limit

        return last_response, saw_rate_limit

    async def _enrich_products_from_details(
        self,
        items: list[dict[str, Any]],
        *,
        timeout: float,
        headers: Mapping[str, str],
        client: httpx.AsyncClient | None = None,
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

        detail_map = await self._fetch_details(
            missing_ids,
            timeout=timeout,
            headers=headers,
            client=client,
        )
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
        self,
        product_ids: Iterable[int],
        *,
        timeout: float,
        headers: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> Mapping[int, dict[str, Any]]:
        id_strings = [str(pid) for pid in product_ids]
        if not id_strings:
            return {}

        request_headers = dict(headers or self._headers)
        request_headers.setdefault("Accept-Encoding", HEADERS["Accept-Encoding"])

        async def _fetch_batches(active_client: httpx.AsyncClient) -> Mapping[int, dict[str, Any]]:
            aggregated: dict[int, dict[str, Any]] = {}
            for start in range(0, len(id_strings), DETAIL_BATCH):
                batch = id_strings[start : start + DETAIL_BATCH]
                params = {
                    "appType": 1,
                    "curr": "rub",
                    "dest": -1257786,
                    "nm": ",".join(batch),
                }
                response, _ = await self._request_with_backoff(
                    active_client,
                    "GET",
                    DETAIL_API_URL,
                    headers=request_headers,
                    timeout=timeout,
                    params=params,
                )
                if response is None:
                    continue
                try:
                    payload = response.json()
                except Exception:  # noqa: BLE001
                    logger.error(
                        "Не удалось разобрать JSON детализации WB: статус=%s body[:200]=%r",
                        response.status_code,
                        (response.text or "")[:200],
                    )
                    continue

                details = payload.get("data", {}).get("products", [])
                for item in details:
                    if isinstance(item, Mapping) and item.get("id") is not None:
                        try:
                            aggregated[int(item.get("id"))] = dict(item)
                        except (TypeError, ValueError):
                            continue
            return aggregated

        if client is None:
            async with httpx.AsyncClient(
                timeout=timeout,
                headers=request_headers,
                follow_redirects=True,
                http2=self._http2_enabled,
            ) as local_client:
                return await _fetch_batches(local_client)

        return await _fetch_batches(client)

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
