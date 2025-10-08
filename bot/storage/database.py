from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional


class Database:
    """Simple SQLite wrapper for storing subscription data."""

    def __init__(self, url: str) -> None:
        if not url.startswith("sqlite:///"):
            raise ValueError("Only sqlite:/// URLs are supported")

        db_path = Path(url.replace("sqlite:///", ""))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path)
        try:
            connection.row_factory = sqlite3.Row
            yield connection
            connection.commit()
        finally:
            connection.close()

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS subscriptions (
                    user_id INTEGER PRIMARY KEY,
                    plan TEXT NOT NULL,
                    expires_at TEXT
                );
                """
            )

    def upsert_subscription(
        self, user_id: int, plan: str, expires_at: Optional[datetime]
    ) -> None:
        expires = expires_at.isoformat() if expires_at else None
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO subscriptions (user_id, plan, expires_at)
                VALUES (:user_id, :plan, :expires_at)
                ON CONFLICT(user_id) DO UPDATE SET
                    plan=excluded.plan,
                    expires_at=excluded.expires_at;
                """,
                {"user_id": user_id, "plan": plan, "expires_at": expires},
            )

    def get_subscription(self, user_id: int) -> Optional[dict[str, Optional[str]]]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT plan, expires_at FROM subscriptions WHERE user_id = ?",
                (user_id,),
            ).fetchone()

        if row is None:
            return None

        return {"plan": row["plan"], "expires_at": row["expires_at"]}
