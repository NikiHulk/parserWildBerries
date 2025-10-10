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
