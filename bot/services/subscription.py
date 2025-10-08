from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from ..storage.database import Database


@dataclass(frozen=True)
class SubscriptionPlan:
    name: str
    duration_days: int
    description: str
    price_rub: int


DEFAULT_PLANS = {
    "trial": SubscriptionPlan(
        "trial",
        7,
        "Бесплатный пробный период на 7 дней.",
        price_rub=0,
    ),
    "monthly": SubscriptionPlan(
        "monthly",
        30,
        "Месячная подписка для расширенного поиска.",
        price_rub=1490,
    ),
    "annual": SubscriptionPlan(
        "annual",
        365,
        "Годовая подписка с максимальными возможностями.",
        price_rub=14990,
    ),
}


class SubscriptionService:
    def __init__(self, database: Database) -> None:
        self._db = database
        self._db.migrate()

    def activate(
        self, user_id: int, plan_name: str, custom_duration: Optional[int] = None
    ) -> datetime:
        plan = DEFAULT_PLANS.get(plan_name)
        if plan is None and custom_duration is None:
            raise ValueError("Неизвестный план подписки")

        if plan is not None:
            duration = custom_duration or plan.duration_days
        else:
            duration = custom_duration or 30

        expires_at = datetime.utcnow() + timedelta(days=duration)
        self._db.upsert_subscription(user_id, plan_name, expires_at)
        return expires_at

    def get_plan(self, plan_name: str) -> SubscriptionPlan:
        try:
            return DEFAULT_PLANS[plan_name]
        except KeyError as exc:  # pragma: no cover - defensive branch
            raise ValueError("Неизвестный план подписки") from exc

    def has_active_subscription(self, user_id: int) -> bool:
        data = self._db.get_subscription(user_id)
        if not data:
            return False

        expires_at_raw = data.get("expires_at")
        if expires_at_raw is None:
            return False

        expires_at = datetime.fromisoformat(expires_at_raw)
        return datetime.utcnow() < expires_at

    def get_subscription_summary(self, user_id: int) -> str:
        data = self._db.get_subscription(user_id)
        if not data:
            return "У вас нет активной подписки."

        expires_raw = data.get("expires_at")
        expires_at = datetime.fromisoformat(expires_raw) if expires_raw else None
        plan = data.get("plan", "unknown")
        if not expires_at:
            return f"План: {plan}. Дата окончания неизвестна."

        return f"План: {plan}. Подписка действует до {expires_at:%d.%m.%Y %H:%M UTC}."
