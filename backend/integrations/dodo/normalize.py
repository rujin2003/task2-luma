"""Normalize provider payloads before they enter treasury models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PaymentEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    payment_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    amount_minor: int
    currency: str = Field(min_length=3, max_length=3)
    occurred_at: datetime
    failure_code: str | None = None
    customer_id: str | None = None
    subscription_id: str | None = None


def normalize_payment(payload: dict[str, Any]) -> PaymentEvent:
    """Validate the normalized subset consumed by the finance pipeline."""
    return PaymentEvent.model_validate(
        {
            "payment_id": payload.get("payment_id", payload.get("id")),
            "status": payload.get("status"),
            "amount_minor": payload.get("amount_minor", payload.get("amount")),
            "currency": str(payload.get("currency", "")).upper(),
            "occurred_at": payload.get(
                "occurred_at", payload.get("updated_at", payload.get("created_at"))
            ),
            "failure_code": payload.get("failure_code"),
            "customer_id": payload.get("customer_id"),
            "subscription_id": payload.get("subscription_id"),
        }
    )


__all__ = ["PaymentEvent", "normalize_payment"]
