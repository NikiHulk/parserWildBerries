from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    telegram_token: str = Field(..., env="TELEGRAM_TOKEN")
    database_url: str = Field(
        default=f"sqlite:///{Path('data') / 'bot.db'}",
        env="DATABASE_URL",
    )
    request_timeout: float = Field(default=10.0, env="REQUEST_TIMEOUT")
    max_results: int = Field(default=10, env="MAX_RESULTS")
    tg_results_per_page: int = Field(
        default=8,
        env="TG_RESULTS_PER_PAGE",
    )
    tg_welcome_text: str = Field(
        default="Добро пожаловать! Нажмите «🔎 Поиск», чтобы найти товар.",
        env="TG_WELCOME_TEXT",
    )
    tg_top_k: int = Field(default=3, env="TG_TOP_K")
    tg_cancel_text: str = Field(default="❌ Отмена", env="TG_CANCEL_TEXT")
    tg_skip_text: str = Field(default="⏭ Пропустить", env="TG_SKIP_TEXT")
    tg_search_timeout: int = Field(default=120, env="TG_SEARCH_TIMEOUT")
    tg_search_max_pages: int = Field(default=2, env="TG_SEARCH_MAX_PAGES")
    tg_progress_interval: int = Field(default=4, env="TG_PROGRESS_INTERVAL")
    tg_progress_text: str = Field(
        default="Ищу подходящие товары...",
        env="TG_PROGRESS_TEXT",
    )
    wb_detail_max_batch: int = Field(default=1, env="WB_DETAIL_MAX_BATCH")
    wb_detail_split_on_empty: bool = Field(
        default=True, env="WB_DETAIL_SPLIT_ON_EMPTY"
    )
    html_max_page_ms: int = Field(default=45000, env="HTML_MAX_PAGE_MS")
    min_rating: float | None = Field(default=None, env="MIN_RATING")
    min_feedbacks: int | None = Field(default=None, env="MIN_FEEDBACKS")
    min_discount: float | None = Field(default=None, env="MIN_DISCOUNT")
    yookassa_shop_id: str | None = Field(default=None, env="YOOKASSA_SHOP_ID")
    yookassa_secret_key: str | None = Field(
        default=None,
        env="YOOKASSA_SECRET_KEY",
    )
    yookassa_return_url: str | None = Field(
        default=None,
        env="YOOKASSA_RETURN_URL",
    )

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",  # разрешаем сторонние переменные окружения
    )


@lru_cache()
def get_settings() -> Settings:
    env_file = Path(".env")
    kwargs = {}
    if env_file.exists():
        kwargs["_env_file"] = env_file
    return Settings(**kwargs)
