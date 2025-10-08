from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from yookassa import Configuration, Payment

from ..storage.database import Database

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PaymentInfo:
    payment_id: str
    confirmation_url: str
    amount: Decimal
    currency: str
    status: str


class PaymentService:
    """Wrapper around YooKassa payments for subscription plans."""

    def __init__(
        self,
        *,
        shop_id: Optional[str],
        secret_key: Optional[str],
        return_url: Optional[str],
        database: Database,
    ) -> None:
        self._enabled = bool(shop_id and secret_key and return_url)
        self._return_url = return_url
        self._db = database
        if self._enabled:
            Configuration.configure(shop_id, secret_key)  # type: ignore[arg-type]
        else:
            logger.warning(
                "YooKassa credentials are not fully configured. Payments are disabled."
            )

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def create_payment(
        self,
        *,
        user_id: int,
        plan_name: str,
        amount_rub: int,
    ) -> PaymentInfo:
        if not self._enabled:
            raise RuntimeError("Платёжный сервис отключён")

        value = Decimal(amount_rub).quantize(Decimal("1."))
        payload = {
            "amount": {"value": f"{value:.2f}", "currency": "RUB"},
            "capture": True,
            "confirmation": {
                "type": "redirect",
                "return_url": self._return_url,
            },
            "description": f"Подписка {plan_name}",
            "metadata": {
                "plan": plan_name,
                "user_id": user_id,
            },
        }

        payment = await asyncio.to_thread(
            Payment.create,
            payload,
            str(uuid4()),
        )

        info = PaymentInfo(
            payment_id=payment.id,
            confirmation_url=payment.confirmation.confirmation_url,
            amount=value,
            currency=payment.amount.currency,
            status=payment.status,
        )
        self._db.add_payment(
            payment_id=info.payment_id,
            user_id=user_id,
            plan=plan_name,
            status=info.status,
            created_at=datetime.utcnow(),
        )
        return info

    async def refresh_payment(self, payment_id: str) -> Optional[dict[str, str]]:
        if not self._enabled:
            return None

        payment = await asyncio.to_thread(Payment.find_one, payment_id)
        status = payment.status
        metadata = getattr(payment, "metadata", None) or {}
        self._db.update_payment_status(payment_id, status)
        return {
            "status": status,
            "plan": metadata.get("plan", ""),
            "user_id": str(metadata.get("user_id", "")),
        }

    def get_stored_payment(self, payment_id: str) -> Optional[dict[str, Optional[str]]]:
        return self._db.get_payment(payment_id)
