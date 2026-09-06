from __future__ import annotations

from datetime import date

from sqlalchemy import CHAR, BigInteger, CheckConstraint, UniqueConstraint
from sqlmodel import Field, SQLModel

from backend.models.base import (
    Bitemporal,
    Sourced,
    TenantOwned,
    new_id,
    non_negative,
    one_of,
    source_unique,
    valid_currency,
)
from backend.models.vocab import SUBSCRIPTION_INTERVALS, SUBSCRIPTION_STATUSES


class Subscription(TenantOwned, Bitemporal, Sourced, SQLModel, table=True):
    """Recurring billing. `amount_minor` is the non-negative charge per interval."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        source_unique("subscriptions"),
        UniqueConstraint(
            "tenant_id", "company_id", "external_id", name="uq_subscriptions_external"
        ),
        non_negative("amount_minor", "subscriptions"),
        valid_currency("currency", "subscriptions"),
        one_of("interval", SUBSCRIPTION_INTERVALS, "subscriptions"),
        one_of("status", SUBSCRIPTION_STATUSES, "subscriptions"),
        CheckConstraint(
            "started_on IS NULL OR next_billing_date >= started_on",
            name="ck_subscriptions_billing_after_start",
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    customer_id: str = Field(foreign_key="customers.id", index=True)
    external_id: str | None = Field(default=None, max_length=64)
    amount_minor: int = Field(sa_type=BigInteger)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    interval: str = Field(default="monthly", max_length=16)
    started_on: date | None = None
    next_billing_date: date = Field(index=True)
    status: str = Field(default="active", max_length=32, index=True)
