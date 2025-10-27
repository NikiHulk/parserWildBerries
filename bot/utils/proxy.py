"""Вспомогательные функции и классы для работы с прокси."""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from itertools import cycle
from typing import Dict, Iterable, Iterator, Tuple
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_ProxyCycleEntry = Tuple[Tuple[str, ...], Iterator[str]]
_PROXY_STATE: Dict[str, _ProxyCycleEntry] = {}


def sanitize_proxy(raw: str | None) -> str | None:
    """Маскирует чувствительные данные прокси для логов."""

    if not raw:
        return None

    parsed = urlparse(raw)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    scheme = f"{parsed.scheme}://" if parsed.scheme else ""

    if parsed.username:
        auth = f"***:***@" if parsed.password else f"***@"
    else:
        auth = ""

    if not host:
        return raw

    return f"{scheme}{auth}{host}{port}"


def parse_proxy_url(raw: str | None) -> dict[str, str] | None:
    """Преобразует строку прокси в формат Playwright."""

    if not raw:
        return None

    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.hostname or not parsed.port:
        raise ValueError(f"Некорректный адрес прокси: {raw!r}")

    proxy: dict[str, str] = {
        "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
    }
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy


def make_httpx_transport(raw: str | None) -> httpx.AsyncHTTPTransport | None:
    """Создаёт transport для httpx.AsyncClient с учётом прокси."""

    if not raw:
        return None
    return httpx.AsyncHTTPTransport(proxy=raw)


def _build_cycle(values: Tuple[str, ...]) -> Iterator[str]:
    return cycle(values)


def choose_proxy_from_pool(env_key: str = "PROXY_POOL") -> str | None:
    """Совместимость: возвращает очередной прокси из пула окружения."""

    pool_raw = os.getenv(env_key, "")
    entries = tuple(item.strip() for item in pool_raw.split(",") if item.strip())
    if not entries:
        _PROXY_STATE.pop(env_key, None)
        return None

    cached = _PROXY_STATE.get(env_key)
    if cached is None or cached[0] != entries:
        _PROXY_STATE[env_key] = (entries, _build_cycle(entries))

    _, iterator = _PROXY_STATE[env_key]
    return next(iterator)


class ProxyRotator:
    """Управляет списком прокси и журналирует их ротацию."""

    def __init__(
        self,
        proxies: Iterable[str] | None = None,
        *,
        fallback: Iterable[str] | None = None,
        sticky_range: tuple[float, float] = (60.0, 120.0),
    ) -> None:
        unique: list[str] = []
        for source in (proxies or []):
            value = (source or "").strip()
            if value and value not in unique:
                unique.append(value)

        for source in (fallback or []):
            value = (source or "").strip()
            if value and value not in unique:
                unique.append(value)

        self._pool: Tuple[str, ...] = tuple(unique)
        self._index = 0
        self._current: str | None = self._pool[0] if self._pool else None
        self._lock = threading.Lock()
        self._sticky_range = sticky_range
        self._next_rotation_deadline = (
            time.monotonic() + random.uniform(*sticky_range)
            if self._current and sticky_range[0] > 0
            else 0.0
        )

    @property
    def has_pool(self) -> bool:
        return len(self._pool) > 1

    def current(self) -> str | None:
        with self._lock:
            return self._current

    def _schedule_next_window(self) -> None:
        if self._current:
            self._next_rotation_deadline = (
                time.monotonic() + random.uniform(*self._sticky_range)
            )

    def next(self, reason: str, *, force: bool = True) -> str | None:
        """Переходит к следующему прокси и логирует смену."""

        with self._lock:
            prev = self._current
            if not self._pool:
                logger.info("proxy rotate skip reason=%s (pool empty)", reason)
                return prev

            if not force and time.monotonic() < self._next_rotation_deadline:
                logger.info(
                    "proxy rotate suppressed reason=%s prev=%s",  # noqa: PLE1205
                    reason,
                    sanitize_proxy(prev),
                )
                return prev

            if len(self._pool) == 1:
                self._schedule_next_window()
                logger.info(
                    "proxy rotate reason=%s prev=%s next=%s (single)",
                    reason,
                    sanitize_proxy(prev),
                    sanitize_proxy(prev),
                )
                return prev

            self._index = (self._index + 1) % len(self._pool)
            self._current = self._pool[self._index]
            self._schedule_next_window()
            logger.info(
                "proxy rotate reason=%s prev=%s next=%s",
                reason,
                sanitize_proxy(prev),
                sanitize_proxy(self._current),
            )
            return self._current


__all__ = [
    "ProxyRotator",
    "choose_proxy_from_pool",
    "make_httpx_transport",
    "parse_proxy_url",
    "sanitize_proxy",
]
