"""Вспомогательные функции для работы с прокси."""

from __future__ import annotations

import os
from itertools import cycle
from typing import Dict, Iterator, Tuple
from urllib.parse import urlparse

import httpx

_ProxyCycleEntry = Tuple[Tuple[str, ...], Iterator[str]]
_PROXY_STATE: Dict[str, _ProxyCycleEntry] = {}


def parse_proxy_url(raw: str | None) -> dict[str, str] | None:
    """Преобразует строку прокси в формат Playwright.

    >>> parse_proxy_url("http://user:pass@1.2.3.4:8080")['server']
    'http://1.2.3.4:8080'
    >>> parse_proxy_url(None) is None
    True
    """

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


def sanitize_proxy(raw: str | None) -> str | None:
    """Маскирует чувствительные данные прокси для логов."""

    if not raw:
        return None

    parsed = urlparse(raw)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    scheme = f"{parsed.scheme}://" if parsed.scheme else ""

    if parsed.username:
        user = parsed.username
        if parsed.password:
            user = f"{parsed.username}:***"
        auth = f"{user}@"
    else:
        auth = ""

    if not host:
        return raw

    return f"{scheme}{auth}{host}{port}"


def _build_cycle(values: Tuple[str, ...]) -> Iterator[str]:
    return cycle(values)


def choose_proxy_from_pool(env_key: str = "PROXY_POOL") -> str | None:
    """Возвращает очередной прокси из пула в переменной окружения.

    >>> os.environ['PROXY_POOL'] = 'http://a:1, http://b:2'
    >>> first = choose_proxy_from_pool()
    >>> second = choose_proxy_from_pool()
    >>> first != second
    True
    """

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


__all__ = [
    "choose_proxy_from_pool",
    "make_httpx_transport",
    "parse_proxy_url",
    "sanitize_proxy",
]
