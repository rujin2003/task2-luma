"""Normalize Dodo provider payloads into treasury-facing events.

A successful payment is not cash. Cash arrives when the payout settles, after the
balance-ledger lag. These DTOs keep that distinction explicit.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

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


class LedgerEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    entry_id: str = Field(min_length=1)
    payment_id: str | None = None
    entry_type: str = Field(min_length=1)
    amount_minor: int
    currency: str = Field(min_length=3, max_length=3)
    effective_at: datetime


class PayoutEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    payout_id: str = Field(min_length=1)
    amount_minor: int
    currency: str = Field(min_length=3, max_length=3)
    status: str = Field(min_length=1)
    expected_settlement_date: date
    created_at: datetime


CashStage = Literal["payment", "ledger", "payout", "settled"]


class CashTiming(BaseModel):
    """Payment → balance-ledger → payout settlement chain for one payment."""

    model_config = ConfigDict(frozen=True)

    payment_id: str
    amount_minor: int
    currency: str
    payment_at: datetime
    ledger_at: datetime | None
    payout_id: str | None
    settlement_date: date | None
    stage: CashStage
    lag_business_days: int


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
            "failure_code": payload.get("failure_code")
            or payload.get("error_code")
            or payload.get("decline_code"),
            "customer_id": payload.get("customer_id"),
            "subscription_id": payload.get("subscription_id"),
        }
    )


def normalize_ledger_entry(payload: dict[str, Any]) -> LedgerEntry:
    return LedgerEntry.model_validate(
        {
            "entry_id": payload.get("entry_id", payload.get("id")),
            "payment_id": payload.get("payment_id"),
            "entry_type": payload.get("entry_type", payload.get("type", "credit")),
            "amount_minor": payload.get("amount_minor", payload.get("amount")),
            "currency": str(payload.get("currency", "")).upper(),
            "effective_at": payload.get(
                "effective_at", payload.get("created_at", payload.get("occurred_at"))
            ),
        }
    )


def normalize_payout(payload: dict[str, Any]) -> PayoutEvent:
    settlement = payload.get("expected_settlement_date") or payload.get("settlement_date")
    created = payload.get("created_at", payload.get("occurred_at"))
    return PayoutEvent.model_validate(
        {
            "payout_id": payload.get("payout_id", payload.get("id")),
            "amount_minor": payload.get("amount_minor", payload.get("amount")),
            "currency": str(payload.get("currency", "")).upper(),
            "status": payload.get("status", "pending"),
            "expected_settlement_date": settlement,
            "created_at": created,
        }
    )


def _as_date(value: date | datetime) -> date:
    return value if isinstance(value, date) and not isinstance(value, datetime) else value.date()


def expected_settlement_date(
    payment_at: datetime,
    *,
    lag_business_days: int,
    holidays: set[date] | None = None,
) -> date:
    """Advance `lag_business_days` weekdays from the payment date, skipping holidays."""
    if lag_business_days < 0:
        raise ValueError("lag_business_days cannot be negative")
    closed = holidays or set()
    day = _as_date(payment_at)
    remaining = lag_business_days
    while remaining > 0:
        day += timedelta(days=1)
        if day.weekday() >= 5 or day in closed:
            continue
        remaining -= 1
    return day


def resolve_cash_timing(
    payment: PaymentEvent,
    *,
    ledger: list[LedgerEntry],
    payouts: list[PayoutEvent],
    lag_business_days: int,
    holidays: set[date] | None = None,
) -> CashTiming:
    """Map one payment through ledger credit and payout settlement."""
    ledger_hit = next((row for row in ledger if row.payment_id == payment.payment_id), None)
    payout_hit = None
    if ledger_hit is not None:
        # Match by amount+currency when payouts do not carry payment ids.
        for payout in payouts:
            if payout.currency == payment.currency and payout.amount_minor == payment.amount_minor:
                payout_hit = payout
                break

    if payment.status not in {"succeeded", "successful", "paid", "captured"}:
        return CashTiming(
            payment_id=payment.payment_id,
            amount_minor=payment.amount_minor,
            currency=payment.currency,
            payment_at=payment.occurred_at,
            ledger_at=None,
            payout_id=None,
            settlement_date=None,
            stage="payment",
            lag_business_days=lag_business_days,
        )

    settlement = (
        payout_hit.expected_settlement_date
        if payout_hit is not None
        else expected_settlement_date(
            payment.occurred_at, lag_business_days=lag_business_days, holidays=holidays
        )
    )
    if payout_hit is not None and payout_hit.status in {"paid", "settled", "completed"}:
        stage: CashStage = "settled"
    elif payout_hit is not None:
        stage = "payout"
    elif ledger_hit is not None:
        stage = "ledger"
    else:
        stage = "payment"

    return CashTiming(
        payment_id=payment.payment_id,
        amount_minor=payment.amount_minor,
        currency=payment.currency,
        payment_at=payment.occurred_at,
        ledger_at=None if ledger_hit is None else ledger_hit.effective_at,
        payout_id=None if payout_hit is None else payout_hit.payout_id,
        settlement_date=settlement,
        stage=stage,
        lag_business_days=lag_business_days,
    )


__all__ = [
    "CashStage",
    "CashTiming",
    "LedgerEntry",
    "PaymentEvent",
    "PayoutEvent",
    "expected_settlement_date",
    "normalize_ledger_entry",
    "normalize_payment",
    "normalize_payout",
    "resolve_cash_timing",
]
