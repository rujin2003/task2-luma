from __future__ import annotations

from datetime import date

from sqlalchemy import CHAR, BigInteger, CheckConstraint, UniqueConstraint
from sqlmodel import Field, SQLModel

from backend.models.base import (
    Bitemporal,
    Sourced,
    TenantOwned,
    money_pair,
    new_id,
    non_negative,
    source_unique,
    valid_currency,
)


class BankAccount(TenantOwned, Bitemporal, Sourced, SQLModel, table=True):
    """`current_balance_minor` is a projection of settled bank transactions."""

    __tablename__ = "bank_accounts"
    __table_args__ = (
        source_unique("bank_accounts"),
        UniqueConstraint("tenant_id", "company_id", "account_ref", name="uq_bank_accounts_ref"),
        valid_currency("currency", "bank_accounts"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    gl_account_id: str | None = Field(default=None, foreign_key="gl_accounts.id", index=True)
    name: str = Field(max_length=128)
    account_ref: str = Field(max_length=64, index=True)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    restricted: bool = False
    current_balance_minor: int = Field(default=0, sa_type=BigInteger)


class BankTransaction(TenantOwned, Bitemporal, Sourced, SQLModel, table=True):
    """A bank movement. Signed: positive is cash in, negative is cash out."""

    __tablename__ = "bank_transactions"
    __table_args__ = (
        source_unique("bank_transactions"),
        valid_currency("currency", "bank_transactions"),
        CheckConstraint("amount_minor <> 0", name="ck_bank_transactions_nonzero"),
        CheckConstraint("value_date >= booking_date", name="ck_bank_transactions_value_after_book"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    account_id: str = Field(foreign_key="bank_accounts.id", index=True)
    booking_date: date = Field(index=True)
    value_date: date
    amount_minor: int = Field(sa_type=BigInteger)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    pending: bool = False
    counterparty: str | None = Field(default=None, max_length=256)
    description: str | None = Field(default=None, max_length=512)
    category: str | None = Field(default=None, max_length=64)


class BankReconciliation(TenantOwned, Bitemporal, Sourced, SQLModel, table=True):
    """The signed-off reconciliation artifact (WORKFLOW.md §10).

    The two adjusted totals and the difference are stored rather than derived so
    the artifact is immutable evidence, and constrained so a stored total that
    contradicts its own components cannot be written.
    """

    __tablename__ = "bank_reconciliations"
    __table_args__ = (
        source_unique("bank_reconciliations"),
        UniqueConstraint("tenant_id", "account_id", "as_of", name="uq_bank_reconciliations_as_of"),
        valid_currency("currency", "bank_reconciliations"),
        CheckConstraint(
            "adjusted_bank_minor = balance_per_bank_minor - outstanding_cheques_minor "
            "+ deposits_in_transit_minor",
            name="ck_bank_reconciliations_adjusted_bank",
        ),
        CheckConstraint(
            "adjusted_book_minor = balance_per_gl_minor + book_adjustments_minor "
            "- unrecorded_bank_fees_minor",
            name="ck_bank_reconciliations_adjusted_book",
        ),
        CheckConstraint(
            "difference_minor = adjusted_bank_minor - adjusted_book_minor",
            name="ck_bank_reconciliations_difference",
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    account_id: str = Field(foreign_key="bank_accounts.id", index=True)
    as_of: date = Field(index=True)
    balance_per_bank_minor: int = Field(sa_type=BigInteger)
    outstanding_cheques_minor: int = Field(sa_type=BigInteger)
    deposits_in_transit_minor: int = Field(sa_type=BigInteger)
    unrecorded_bank_fees_minor: int = Field(sa_type=BigInteger)
    adjusted_bank_minor: int = Field(sa_type=BigInteger)
    balance_per_gl_minor: int = Field(sa_type=BigInteger)
    book_adjustments_minor: int = Field(sa_type=BigInteger)
    adjusted_book_minor: int = Field(sa_type=BigInteger)
    difference_minor: int = Field(sa_type=BigInteger)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    prepared_by: str = Field(max_length=64)
    reviewed_by: str | None = Field(default=None, max_length=64)
    approved_by: str | None = Field(default=None, max_length=64)


class BankReconciliationItem(TenantOwned, SQLModel, table=True):
    """A single reconciling item, aged. Phase 3 `cash.py` reports these."""

    __tablename__ = "bank_reconciliation_items"
    __table_args__ = (
        money_pair("amount_minor", "currency", "bank_reconciliation_items"),
        valid_currency("currency", "bank_reconciliation_items"),
        non_negative("age_days", "bank_reconciliation_items"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    reconciliation_id: str = Field(foreign_key="bank_reconciliations.id", index=True)
    kind: str = Field(max_length=48)
    description: str = Field(max_length=512)
    amount_minor: int = Field(sa_type=BigInteger)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    age_days: int = 0
    resolved: bool = False
