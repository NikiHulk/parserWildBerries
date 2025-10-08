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

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS banned_words (
                    user_id INTEGER NOT NULL,
                    word TEXT NOT NULL,
                    UNIQUE(user_id, word)
                );
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS payments (
                    payment_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    plan TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
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

    def list_banned_words(self, user_id: int) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT word FROM banned_words WHERE user_id = ? ORDER BY LOWER(word)",
                (user_id,),
            ).fetchall()

        return [row["word"] for row in rows]

    def add_banned_word(self, user_id: int, word: str) -> bool:
        with self.connect() as connection:
            try:
                connection.execute(
                    "INSERT OR IGNORE INTO banned_words (user_id, word) VALUES (?, ?)",
                    (user_id, word),
                )
            except sqlite3.Error:
                return False

            changes = connection.total_changes

        return changes > 0

    def add_payment(
        self,
        *,
        payment_id: str,
        user_id: int,
        plan: str,
        status: str,
        created_at: datetime,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO payments (
                    payment_id, user_id, plan, status, created_at
                ) VALUES (:payment_id, :user_id, :plan, :status, :created_at)
                """,
                {
                    "payment_id": payment_id,
                    "user_id": user_id,
                    "plan": plan,
                    "status": status,
                    "created_at": created_at.isoformat(),
                },
            )

    def update_payment_status(self, payment_id: str, status: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE payments SET status = ? WHERE payment_id = ?",
                (status, payment_id),
            )

    def get_payment(self, payment_id: str) -> Optional[dict[str, Optional[str]]]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT payment_id, user_id, plan, status, created_at
                FROM payments
                WHERE payment_id = ?
                """,
                (payment_id,),
            ).fetchone()

        if row is None:
            return None

        return {
            "payment_id": row["payment_id"],
            "user_id": str(row["user_id"]),
            "plan": row["plan"],
            "status": row["status"],
            "created_at": row["created_at"],
        }

    def remove_banned_word(self, user_id: int, word: str) -> bool:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM banned_words WHERE user_id = ? AND word = ?",
                (user_id, word),
            )
            changes = connection.total_changes

        return changes > 0
