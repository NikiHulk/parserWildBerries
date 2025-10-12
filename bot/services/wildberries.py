from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import random
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, List
from urllib.parse import quote, quote_plus

import httpx

if TYPE_CHECKING:  # pragma: no cover - только для типов
    from playwright.async_api import BrowserContext

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

DEFAULT_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

COOKIE_TTL_SECONDS = 6 * 60 * 60
PLAYWRIGHT_USER_DATA_DIR = Path(
    os.getenv("PLAYWRIGHT_USER_DATA_DIR", "data")
).expanduser()
COOKIE_FILE = PLAYWRIGHT_USER_DATA_DIR / "wb_cookies.json"
PLAYWRIGHT_STORAGE_FILE = Path(
    os.getenv("PLAYWRIGHT_STATE_PATH", "/app/data/wb_playwright_state.json")
)
HTML_SETTLE_MS = int(os.getenv("PLAYWRIGHT_THROTTLE_MS", "800") or "800")

START_LIMIT = 20
MAX_CATALOG_ATTEMPTS = 2
PAGE_DELAY_RANGE = (0.30, 0.70)
DETAIL_BATCH = 100
MAX_HTML_IDS = 120

from bot.utils.proxy import (
    ProxyRotator,
    make_httpx_transport,
    parse_proxy_url,
    sanitize_proxy,
)


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
        f"?search={quote(query)}&page={page}"
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
    slow_mode: bool = False
    proxy: str | None = None
    proxy_rotated: bool = False


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
        self._httpx_proxy_env = (
            os.getenv("HTTPX_PROXY")
            or os.getenv("WB_HTTP_PROXY")
            or os.getenv("HTTP_PROXY")
            or os.getenv("HTTPS_PROXY")
        )
        self._playwright_proxy_env = os.getenv("PLAYWRIGHT_PROXY") or None
        pool_raw = os.getenv("PROXY_POOL", "")
        pool_entries = [item.strip() for item in pool_raw.split(",") if item.strip()]
        fallback_entries: list[str] = []
        for candidate in (self._playwright_proxy_env, self._httpx_proxy_env):
            if candidate and candidate not in pool_entries and candidate not in fallback_entries:
                fallback_entries.append(candidate)

        self._proxy_rotator = ProxyRotator(pool_entries, fallback=fallback_entries)
        self._proxy_current = self._proxy_rotator.current()
        self._active_proxy_raw: str | None = None
        self._http_client_needs_restart = False
        self._http_client: httpx.AsyncClient | None = None
        self._http_client_headers: tuple[tuple[str, str], ...] | None = None
        self._http_client_timeout: float | None = None
        initial_proxy = self._proxy_current or self._httpx_proxy_env
        self._set_active_proxy(initial_proxy, initial=True)
        self._proxy_current = self._active_proxy_raw
        if sanitized := sanitize_proxy(self._proxy_current):
            logger.info("[WB/proxy] Активный прокси %s", sanitized)
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
        self._storage_state_file = PLAYWRIGHT_STORAGE_FILE
        self._html_settle_ms = HTML_SETTLE_MS
        headless_env = os.getenv("PLAYWRIGHT_HEADLESS")
        self._playwright_headless = not (
            headless_env and headless_env.strip().lower() in {"0", "false", "no"}
        )
        self._playwright_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--lang=ru-RU,ru",
            "--disable-blink-features=AutomationControlled",
            "--disable-http2",
            "--disable-features=NetworkServiceInProcess",
        ]
        self._playwright_manager = None
        self._browser = None
        self._browser_context = None
        self._browser_lock = asyncio.Lock()
        self._html_context_user_agent: str | None = None
        self._last_html_ok = False
        self._proxy_pool_defined = self._proxy_rotator.has_pool
        self._proxy_rotated_flag = 0
        self._slow_mode_active = False
        self._consecutive_anti_bot = 0
        self._resource_block_route_installed = False
        self._resource_block_handler = None

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

    def _pick_user_agent(self) -> str:
        if self._user_agent_locked:
            return self._headers.get("User-Agent", USER_AGENTS[0])
        return random.choice(USER_AGENTS)

    def _current_http_proxy_raw(self) -> str | None:
        return self._active_proxy_raw

    def _set_active_proxy(self, proxy_raw: str | None, *, initial: bool = False) -> None:
        resolved = proxy_raw or self._httpx_proxy_env
        if initial:
            self._active_proxy_raw = resolved
            return

        if resolved == self._active_proxy_raw:
            return

        self._active_proxy_raw = resolved
        self._http_client_needs_restart = True

    def _create_http_client(
        self, headers: Mapping[str, str], timeout: float
    ) -> httpx.AsyncClient:
        transport = make_httpx_transport(self._current_http_proxy_raw())
        return httpx.AsyncClient(
            timeout=timeout,
            headers=headers,
            follow_redirects=True,
            http2=self._http2_enabled,
            transport=transport,
        )

    async def _ensure_http_client(
        self, headers: Mapping[str, str], timeout: float
    ) -> httpx.AsyncClient:
        normalized_headers = tuple(sorted(headers.items()))
        if (
            self._http_client is None
            or self._http_client_needs_restart
            or self._http_client_headers != normalized_headers
            or self._http_client_timeout != timeout
        ):
            if self._http_client is not None:
                try:
                    await self._http_client.aclose()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[WB/httpx] Ошибка закрытия клиента: %s", exc)
            self._http_client = self._create_http_client(headers, timeout)
            self._http_client_headers = normalized_headers
            self._http_client_timeout = timeout
            self._http_client_needs_restart = False
        return self._http_client

    def _rotate_proxy(self, reason: str, *, slow_next: bool = False) -> None:
        prev = self._proxy_rotator.current()
        new_proxy = self._proxy_rotator.next(reason)
        if new_proxy != prev:
            self._proxy_rotated_flag = 1
        else:
            self._proxy_rotated_flag = 0
        self._proxy_current = new_proxy or self._httpx_proxy_env
        self._set_active_proxy(self._proxy_current)
        if slow_next:
            self._slow_mode_active = True

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

        page = 1

        try:
            while len(filtered_candidates) < max_results:
                client = await self._ensure_http_client(
                    session_headers, effective_timeout
                )

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

        finally:
            if self._http_client is not None:
                try:
                    await self._http_client.aclose()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[WB/httpx] Ошибка закрытия клиента: %s", exc)
                self._http_client = None
                self._http_client_headers = None
                self._http_client_timeout = None
                self._http_client_needs_restart = False

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

    def _storage_state_is_fresh(self) -> bool:
        try:
            stat = self._storage_state_file.stat()
        except OSError:
            return False
        return time.time() - stat.st_mtime < self._cookie_ttl

    async def _install_blocking_route(self, context: "BrowserContext") -> None:
        if self._resource_block_route_installed:
            return

        async def handler(route):  # type: ignore[no-untyped-def]
            try:
                if route.request.resource_type in {"image", "font", "media", "stylesheet"}:
                    await route.abort()
                else:
                    await route.continue_()
            except Exception:  # noqa: BLE001
                try:
                    await route.abort()
                except Exception:  # noqa: BLE001
                    pass

        await context.route("**/*", handler)
        self._resource_block_route_installed = True
        self._resource_block_handler = handler

    async def _ensure_browser_context(
        self, *, force_refresh: bool = False
    ) -> "BrowserContext":
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error(
                "Playwright не установлен. Установите 'playwright' и выполните 'playwright install chromium'."
            )
            raise

        async with self._browser_lock:
            now = time.time()
            if force_refresh:
                await self._close_browser_context_locked()

            if self._browser_context is not None:
                return self._browser_context

            if self._playwright_manager is None:
                self._playwright_manager = await async_playwright().start()

            if self._browser is None:
                await self._launch_browser_locked()

            storage_state_arg: str | None = None
            cookie_source: str | None = None
            if (
                self._cookie_cache is not None
                and self._cookie_cache_ts is not None
                and now - self._cookie_cache_ts < self._cookie_ttl
                and self._storage_state_is_fresh()
            ):
                storage_state_arg = str(self._storage_state_file)
                cookie_source = f"in-memory {len(self._cookie_cache)}"
            else:
                cookies, ts = await self._load_cookies_from_disk()
                if (
                    cookies is not None
                    and ts is not None
                    and now - ts < self._cookie_ttl
                    and self._storage_state_is_fresh()
                ):
                    self._cookie_cache = cookies
                    self._cookie_cache_ts = ts
                    storage_state_arg = str(self._storage_state_file)
                    cookie_source = f"file {len(cookies)}"

            user_agent = self._pick_user_agent()
            timezone_id = os.getenv("PLAYWRIGHT_TZ", "Europe/Moscow")
            ua_to_use = user_agent or DEFAULT_DESKTOP_UA
            context_kwargs = {
                "user_agent": ua_to_use,
                "locale": "ru-RU",
                "timezone_id": timezone_id,
                "ignore_https_errors": True,
                "extra_http_headers": {
                    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                    "Upgrade-Insecure-Requests": "1",
                },
                "viewport": {"width": 1366, "height": 864},
            }
            context_kwargs.update(
                {
                    "java_script_enabled": True,
                    "permissions": ["geolocation"],
                    "geolocation": {"longitude": 37.6173, "latitude": 55.7558},
                }
            )
            if storage_state_arg:
                context_kwargs["storage_state"] = storage_state_arg
                logger.info(
                    "[WB/Playwright] Использую сохранённые cookies (%s)",
                    cookie_source,
                )
            else:
                logger.info("[WB/Playwright] Запуск нового браузерного контекста")

            self._browser_context = await self._browser.new_context(**context_kwargs)
            await self._install_blocking_route(self._browser_context)
            try:
                await self._browser_context.add_init_script(
                    """
                    Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
                    Object.defineProperty(navigator,'platform',{get:()=> 'Win32'});
                    Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>8});
                    Object.defineProperty(navigator,'deviceMemory',{get:()=>8});
                    Object.defineProperty(navigator,'language',{get:()=> 'ru-RU'});
                    Object.defineProperty(navigator,'languages',{get:()=>['ru-RU','ru','en-US','en']});
                    window.chrome = { runtime: {} };
                    Object.defineProperty(navigator,'plugins',{ get: () => [1, 2, 3] });
                    """
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[WB/Playwright] Не удалось добавить антидетект-скрипт: %s", exc)
            self._html_context_user_agent = user_agent

            if storage_state_arg:
                await self._persist_browser_state(self._browser_context)
            else:
                await self._initialize_browser_context(self._browser_context)

            return self._browser_context

    async def _launch_browser_locked(self) -> None:
        if self._playwright_manager is None:
            return

        launch_kwargs = {
            "headless": self._playwright_headless,
            "args": self._playwright_args,
        }

        proxy_raw: str | None = self._proxy_rotator.current() or self._httpx_proxy_env

        if proxy_raw:
            try:
                launch_kwargs["proxy"] = parse_proxy_url(proxy_raw)
                logger.info(
                    "[WB/Playwright] Используется прокси %s",
                    sanitize_proxy(proxy_raw),
                )
            except ValueError as exc:
                logger.warning(
                    "[WB/Playwright] Некорректный прокси %s: %s",
                    sanitize_proxy(proxy_raw),
                    exc,
                )
                proxy_raw = None

        self._proxy_current = proxy_raw or self._httpx_proxy_env
        self._set_active_proxy(self._proxy_current)
        self._browser = await self._playwright_manager.chromium.launch(**launch_kwargs)

    async def _initialize_browser_context(self, context: "BrowserContext") -> None:
        await self._persist_browser_state(context)

    async def _persist_browser_state(self, context: "BrowserContext") -> None:
        await self._update_cookie_cache_from_context(context)
        storage_path = self._storage_state_file
        try:
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=str(storage_path))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[WB/Playwright] Не удалось сохранить storage_state: %s", exc)

    async def _update_cookie_cache_from_context(
        self, context: "BrowserContext"
    ) -> None:
        async with self._cookie_lock:
            try:
                cookies = await context.cookies()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[WB/Playwright] Не удалось получить cookies: %s", exc)
                return

            filtered = self._filter_cookies(cookies)
            self._cookie_cache = filtered
            self._cookie_cache_ts = time.time()
            await self._save_cookies_to_disk(filtered)

    async def _close_browser_context_locked(self) -> None:
        if self._browser_context is not None:
            try:
                await self._browser_context.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[WB/Playwright] Ошибка закрытия контекста: %s", exc)
            self._browser_context = None
            self._resource_block_route_installed = False
            self._resource_block_handler = None

        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[WB/Playwright] Ошибка закрытия браузера: %s", exc)
            self._browser = None

        self._html_context_user_agent = None

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
            "slow": bool(page_meta.slow_mode),
            "proxy": page_meta.proxy or sanitize_proxy(self._proxy_current),
            "proxy_rotated": page_meta.proxy_rotated or self._proxy_rotated_flag,
        }
        self._last_page_logs.append(summary)
        proxy_used = summary["proxy"]
        proxy_rotated = summary["proxy_rotated"]
        self._proxy_rotated_flag = 0

        extra_parts: list[str] = []
        if page_meta.source in {"html_xhr", "html_dom"}:
            if page_meta.html_ids is not None:
                extra_parts.append(f"ids={page_meta.html_ids}")
            if page_meta.html_enriched is not None:
                extra_parts.append(f"enriched={page_meta.html_enriched}")
        if proxy_used:
            extra_parts.append(f"proxy={proxy_used}")
        if proxy_rotated:
            extra_parts.append("proxy_rotated=1")
        extra_suffix = f" {' '.join(extra_parts)}" if extra_parts else ""

        timing_int = int(page_meta.timing_ms) if page_meta.timing_ms else 0
        logger.info(
            "WB page=%s source=%s status=%s products=%s limit=%s dest=%s spp=%s "
            "cache=%s slow=%s timing=%sms%s",
            page,
            page_meta.source,
            page_meta.status,
            before_count,
            page_meta.limit,
            page_meta.dest,
            page_meta.spp,
            page_meta.cache,
            bool(page_meta.slow_mode),
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
                        slow_mode=False,
                        proxy=sanitize_proxy(self._proxy_current),
                        proxy_rotated=bool(self._proxy_rotated_flag),
                    )

                # Первая попытка каталога
                url_main = url_catalog(query, page, limit, dest, spp)
                response, saw_429 = await self._request_with_backoff(
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
                        slow_mode=encountered_429,
                        proxy=sanitize_proxy(self._proxy_current),
                        proxy_rotated=bool(self._proxy_rotated_flag),
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
                        slow_mode=encountered_429,
                        proxy=sanitize_proxy(self._proxy_current),
                        proxy_rotated=bool(self._proxy_rotated_flag),
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
        """HTML fallback using Playwright with safe launch args and HTTP/2 disabled."""
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        html_url = url_html_search(query, page)
        nm_ids: list[int] = []
        source = "html_xhr"
        max_attempts = 6
        attempt = 0
        slow_mode_used = self._slow_mode_active
        ids_from_xhr: list[int] = []
        ids_from_dom: list[int] = []
        last_html_length = 0
        last_html_anti_bot = False

        def anti_bot_detected(status: int | None, body: str | None) -> bool:
            if status in {403, 497, 498}:
                return True
            if not body:
                return False
            pattern = re.compile(
                r"(доступ ограничен|request blocked|captcha|challenge|forbidden|too many requests|497|498|403)",
                re.IGNORECASE,
            )
            return bool(pattern.search(body))

        while attempt < max_attempts and not nm_ids:
            attempt += 1
            proxy_for_log = sanitize_proxy(
                self._proxy_current or self._current_http_proxy_raw()
            )
            slow_flag = self._slow_mode_active or attempt > 1
            slow_mode_used = slow_mode_used or slow_flag
            logger.info(
                '[WB/Playwright] html_fallback: page=%s query="%s" proxy=%s try=%s/%s slow=%s',
                page,
                query,
                proxy_for_log or "-",
                attempt,
                max_attempts,
                slow_flag,
            )

            try:
                context = await self._ensure_browser_context(force_refresh=False)
            except Exception as exc:  # noqa: BLE001
                logger.error("[WB/Playwright] Не удалось подготовить контекст: %s", exc)
                await asyncio.sleep(random.uniform(1.2, 2.4))
                continue

            page_obj = await context.new_page()
            page_obj.set_default_navigation_timeout(60000)
            page_obj.set_default_timeout(30000)

            responses: list[dict[str, Any]] = []
            last_status: int | None = None
            html_block_detected = False
            rotated_in_exception = False
            html_text_snapshot = ""
            ids_from_xhr = []
            ids_from_dom = []

            async def capture_response(resp) -> None:  # type: ignore[no-untyped-def]
                try:
                    url_value = resp.url
                    if url_value.startswith("https://www.wildberries.ru/catalog/") and resp.status == 200:
                        self._last_html_ok = True
                    if (
                        ("search" in url_value or "catalog" in url_value)
                        and ("wbxcatalog-ru" in url_value or "catalog.wb.ru" in url_value)
                    ):
                        content_type = (resp.headers or {}).get("content-type", "")
                        if resp.status == 200 and "application/json" in content_type:
                            data = await resp.json()
                            products = data.get("data", {}).get("products")
                            if isinstance(products, Sequence) and products:
                                responses.append(data)
                except Exception:  # noqa: BLE001
                    return

            page_obj.on("response", capture_response)

            async def resource_gate(route):  # type: ignore[no-untyped-def]
                try:
                    if route.request.resource_type in {"image", "media", "font"}:
                        await route.abort()
                    else:
                        await route.continue_()
                except Exception:  # noqa: BLE001
                    try:
                        await route.abort()
                    except Exception:  # noqa: BLE001
                        pass

            try:
                await page_obj.route("**/*", resource_gate)
            except Exception:  # noqa: BLE001
                pass

            try:
                goto_response = await page_obj.goto(
                    html_url,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                last_status = goto_response.status if goto_response is not None else None
                try:
                    html_text_snapshot = await page_obj.content()
                except Exception:  # noqa: BLE001
                    html_text_snapshot = ""

                last_html_length = len(html_text_snapshot)
                html_block_detected = anti_bot_detected(last_status, html_text_snapshot)
                last_html_anti_bot = html_block_detected

                if html_block_detected:
                    html_block_detected = True
                    logger.warning(
                        "[WB/Playwright] anti-bot: status=%s pattern=%s proxy=%s",
                        last_status,
                        bool(html_text_snapshot),
                        proxy_for_log or "-",
                    )
                else:
                    await page_obj.wait_for_timeout(random.randint(1200, 2000))
                    try:
                        await page_obj.wait_for_selector("input#searchInput", timeout=5000)
                    except PlaywrightTimeoutError:
                        pass
                    for _ in range(3):
                        try:
                            await page_obj.mouse.wheel(0, random.randint(900, 1400))
                        except Exception:  # noqa: BLE001
                            pass
                        await page_obj.wait_for_timeout(random.randint(500, 900))

                    if responses:
                        logger.info("[WB/Playwright] JSON XHR найден: %s", len(responses))
                        collected: list[int] = []
                        for payload in responses:
                            for product in payload.get("data", {}).get("products", []):
                                if isinstance(product, Mapping) and product.get("id") is not None:
                                    try:
                                        collected.append(int(product.get("id")))
                                    except (TypeError, ValueError):
                                        continue
                        if collected:
                            ids_from_xhr = list(dict.fromkeys(collected))
                            nm_ids = list(ids_from_xhr)
                            source = "html_xhr"

                    if not nm_ids:
                        try:
                            hrefs = await page_obj.eval_on_selector_all(
                                'a[href*="/catalog/"][href$="/detail.aspx"]',
                                "els => els.slice(0,60).map(a => a.href)",
                            )
                        except Exception:  # noqa: BLE001
                            hrefs = []
                        logger.info(
                            "[WB/Playwright] DOM карточек: %s, XHR карточек: %s",
                            len(hrefs or []),
                            len(responses),
                        )
                        dom_collected: list[int] = []
                        for href in hrefs or []:
                            match = re.search(r"/catalog/(\d+)/detail\.aspx", href or "")
                            if match:
                                dom_collected.append(int(match.group(1)))
                        if dom_collected:
                            ids_from_dom = list(dict.fromkeys(dom_collected))
                            nm_ids = list(ids_from_dom)
                            source = "html_dom"
                            logger.info(
                                "[WB/Playwright] HTML DOM fallback recovered %d ids",
                                len(ids_from_dom),
                            )

                if not nm_ids and not html_block_detected:
                    html_block_detected = True

            except PlaywrightTimeoutError as exc:
                logger.warning(
                    "[WB/Playwright] timeout: stage=goto error=%s proxy=%s",
                    exc,
                    proxy_for_log or "-",
                )
                html_block_detected = True
                rotated_in_exception = True
                self._consecutive_anti_bot += 1
                if self._proxy_pool_defined or self._proxy_current or self._httpx_proxy_env:
                    self._rotate_proxy("pw_timeout", slow_next=True)
                else:
                    self._slow_mode_active = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("[WB/Playwright] Ошибка Playwright: %s", exc)
                html_block_detected = True
                rotated_in_exception = True
                self._consecutive_anti_bot += 1
                if self._proxy_pool_defined or self._proxy_current or self._httpx_proxy_env:
                    self._rotate_proxy("pw_error", slow_next=True)
                else:
                    self._slow_mode_active = True
            finally:
                try:
                    page_obj.off("response", capture_response)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    page_obj.unroute("**/*", resource_gate)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await page_obj.close()
                except Exception:  # noqa: BLE001
                    pass

            if html_block_detected:
                if not rotated_in_exception:
                    self._consecutive_anti_bot += 1
                    slow_next = self._consecutive_anti_bot >= 3
                    if self._proxy_pool_defined or self._proxy_current or self._httpx_proxy_env:
                        self._rotate_proxy(
                            f"pw_antibot_{last_status or 'unknown'}",
                            slow_next=slow_next,
                        )
                    elif slow_next:
                        self._slow_mode_active = True
                slow_next_flag = self._consecutive_anti_bot >= 3
                if slow_next_flag and not self._proxy_pool_defined and not rotated_in_exception:
                    self._slow_mode_active = True
                self._http_client_needs_restart = True
                await self._close_browser_context_locked()
                await asyncio.sleep(min(0.4 * attempt, 2.0) + random.uniform(0.0, 0.2))
                continue

            self._consecutive_anti_bot = 0
            self._slow_mode_active = False

        if self._browser_context is not None and nm_ids:
            await self._persist_browser_state(self._browser_context)

        nm_ids = nm_ids[:MAX_HTML_IDS]

        if not nm_ids:
            proxy_for_empty = sanitize_proxy(
                self._proxy_current or self._current_http_proxy_raw()
            )
            logger.info(
                "source=%s page=%s status=html_empty ids=0 enriched=0 slow=%s proxy=%s timing=%dms anti_bot=%s len_html=%s ids_found=0",
                source,
                page,
                self._slow_mode_active or slow_mode_used,
                proxy_for_empty or "-",
                int((time.monotonic() - start_time) * 1000),
                last_html_anti_bot,
                last_html_length,
            )
            return PageFetchMeta(
                products=[],
                source=source,
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
                slow_mode=self._slow_mode_active or slow_mode_used,
                proxy=sanitize_proxy(self._proxy_current or self._current_http_proxy_raw()),
                proxy_rotated=bool(self._proxy_rotated_flag),
            )

        detail_map = await self._fetch_details(
            nm_ids,
            timeout=timeout,
            headers=headers,
            client=client,
        )

        enriched: list[dict[str, Any]] = []
        enriched_count = 0
        for nm_id in nm_ids:
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

        timing_ms = (time.monotonic() - start_time) * 1000
        proxy_for_meta = sanitize_proxy(
            self._proxy_current or self._current_http_proxy_raw()
        )
        ids_found = len(ids_from_xhr) if ids_from_xhr else len(ids_from_dom)
        if not ids_found:
            ids_found = len(nm_ids)
        logger.info(
            "source=%s page=%s status=ok ids=%s enriched=%s slow=%s proxy=%s timing=%dms anti_bot=%s len_html=%s ids_found=%s",
            source,
            page,
            len(nm_ids),
            enriched_count,
            self._slow_mode_active or slow_mode_used,
            proxy_for_meta or "-",
            int(timing_ms),
            last_html_anti_bot,
            last_html_length,
            ids_found,
        )

        return PageFetchMeta(
            products=enriched,
            source=source,
            url=html_url,
            dest=None,
            spp=None,
            status="ok",
            cache="miss",
            had_429=encountered_429,
            limit=limit,
            timing_ms=timing_ms,
            html_ids=len(nm_ids),
            html_enriched=enriched_count,
            slow_mode=self._slow_mode_active or slow_mode_used,
            proxy=proxy_for_meta,
            proxy_rotated=bool(self._proxy_rotated_flag),
        )
    async def _request_with_backoff(
        self,
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
        consecutive_errors = 0
        for attempt in range(max_retries):
            client = await self._ensure_http_client(headers, timeout)
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
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    self._rotate_proxy("httpx_timeout")
                    consecutive_errors = 0
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
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    self._rotate_proxy(f"httpx_status_{status}")
                    consecutive_errors = 0
                await asyncio.sleep(sleep_time)
                continue

            if 200 <= status < 300:
                consecutive_errors = 0
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
            local_transport = make_httpx_transport(self._current_http_proxy_raw())
            async with httpx.AsyncClient(
                timeout=timeout,
                headers=request_headers,
                follow_redirects=True,
                http2=self._http2_enabled,
                transport=local_transport,
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
