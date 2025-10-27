from __future__ import annotations

from typing import Iterable

from ..storage.database import Database


class BannedWordsService:
    """Service for managing user-specific banned word filters."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def list_words(self, user_id: int) -> list[str]:
        return self._database.list_banned_words(user_id)

    def add_word(self, user_id: int, word: str) -> bool:
        normalized = self._normalize_word(word)
        if not normalized:
            return False
        return self._database.add_banned_word(user_id, normalized)

    def add_words(self, user_id: int, words: Iterable[str]) -> int:
        added = 0
        for word in words:
            if self.add_word(user_id, word):
                added += 1
        return added

    def remove_word(self, user_id: int, word: str) -> bool:
        normalized = self._normalize_word(word)
        if not normalized:
            return False
        return self._database.remove_banned_word(user_id, normalized)

    @staticmethod
    def _normalize_word(word: str) -> str:
        return word.strip().lower()
