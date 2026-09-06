from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from sqlalchemy import CHAR, BigInteger, CheckConstraint, UniqueConstraint
from sqlmodel import Field, SQLModel

from backend.models.base import (
    Bitemporal,
    Sourced,
    TenantOwned,
    new_id,
    non_negative,
    source_unique,
    valid_currency,
)

CANONICAL_ROLES = (
    "cash_operating",
    "cash_restricted",
    "cash_mmf",
    "ar_trade",
    "ar_intercompany",
    "ar_other",
    "deferred_revenue",
    "ap_trade",
    "accrued_payroll",
    "taxes_payable",
    "accrued_expenses",
    "revolver_drawn",
    "term_debt",
    "interest_expense",
    "fx_gain_loss",
    "revenue",
    # The P&L counterpart to every non-financing cash outflow. Without one the
    # ledger cannot balance: paying rent debits *something*, and forcing it into
    # a balance-sheet role would silently corrupt AP or accrued expenses.
    "operating_expense",
    "opening_equity",
)

CASH_ROLES = ("cash_operating", "cash_restricted", "cash_mmf")

_ROLE_LIST = ", ".join(f"'{role}'" for role in CANONICAL_ROLES)


class GLAccount(TenantOwned, Sourced, SQLModel, table=True):
    __tablename__ = "gl_accounts"
    __table_args__ = (
        source_unique("gl_accounts"),
        UniqueConstraint("tenant_id", "company_id", "account_number", name="uq_gl_accounts_number"),
        valid_currency("currency", "gl_accounts"),
        CheckConstraint(f"role IN ({_ROLE_LIST})", name="ck_gl_accounts_role"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    account_number: str = Field(max_length=32, index=True)
    name: str = Field(max_length=128)
    role: str = Field(max_length=32, index=True)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    normal_debit: bool = True


class JournalEntry(TenantOwned, Bitemporal, Sourced, SQLModel, table=True):
    """A journal entry header — the unit that must balance.

    Without a header, `journal_ref` is a free string and nothing ties a set of
    lines together, so "debits equal credits" is unstateable and unenforceable.
    Every `GLTransaction` belongs to exactly one entry; balance is asserted by
    `assert_balanced` at post time and re-checked by the reconciliation gate.
    """

    __tablename__ = "journal_entries"
    __table_args__ = (
        source_unique("journal_entries"),
        UniqueConstraint("tenant_id", "company_id", "entry_ref", name="uq_journal_entries_ref"),
        valid_currency("currency", "journal_entries"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    period_id: str | None = Field(default=None, foreign_key="accounting_periods.id", index=True)
    entry_ref: str = Field(max_length=64, index=True)
    txn_date: date = Field(index=True)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    memo: str | None = Field(default=None, max_length=512)
    reversal_of_id: str | None = Field(default=None, foreign_key="journal_entries.id")


class GLTransaction(TenantOwned, Bitemporal, Sourced, SQLModel, table=True):
    """One side of a journal entry. Exactly one of debit/credit is positive."""

    __tablename__ = "gl_transactions"
    __table_args__ = (
        source_unique("gl_transactions"),
        non_negative("debit_minor", "gl_transactions"),
        non_negative("credit_minor", "gl_transactions"),
        valid_currency("currency", "gl_transactions"),
        CheckConstraint(
            "(debit_minor > 0 AND credit_minor = 0) OR (credit_minor > 0 AND debit_minor = 0)",
            name="ck_gl_transactions_single_sided",
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    entry_id: str = Field(foreign_key="journal_entries.id", index=True)
    account_id: str = Field(foreign_key="gl_accounts.id", index=True)
    txn_date: date = Field(index=True)
    debit_minor: int = Field(default=0, sa_type=BigInteger)
    credit_minor: int = Field(default=0, sa_type=BigInteger)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    memo: str | None = Field(default=None, max_length=512)
    line_no: int = 0


class UnbalancedJournal(ValueError):
    """Raised when a journal entry's debits and credits do not tie."""


def assert_balanced(entry_ref: str, lines: Iterable[GLTransaction]) -> None:
    """Reject a journal entry whose debits and credits do not tie.

    This is the one accounting invariant that cannot be expressed as a row-level
    CHECK, so it is enforced at the only place lines are created.
    """
    materialised = list(lines)
    if not materialised:
        raise UnbalancedJournal(f"journal {entry_ref} has no lines")
    currencies = {line.currency for line in materialised}
    if len(currencies) != 1:
        raise UnbalancedJournal(f"journal {entry_ref} mixes currencies: {sorted(currencies)}")
    debits = sum(line.debit_minor for line in materialised)
    credits = sum(line.credit_minor for line in materialised)
    if debits != credits:
        raise UnbalancedJournal(
            f"journal {entry_ref} does not balance: debits {debits} != credits {credits}"
        )
