from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    telegram_token: str = Field(..., env="TELEGRAM_TOKEN")
    database_url: str = Field(
        default=f"sqlite:///{Path('data') / 'bot.db'}",
        env="DATABASE_URL",
    )
    request_timeout: float = Field(default=10.0, env="REQUEST_TIMEOUT")
    max_results: int = Field(default=10, env="MAX_RESULTS")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


@lru_cache()
def get_settings() -> Settings:
    return Settings()
