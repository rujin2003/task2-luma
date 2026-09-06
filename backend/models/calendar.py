from __future__ import annotations

from datetime import date

from sqlalchemy import CHAR, BigInteger, CheckConstraint, UniqueConstraint
from sqlmodel import Field, SQLModel

from backend.models.base import (
    TenantOwned,
    money_pair,
    new_id,
    one_of,
    valid_currency,
)
from backend.models.vocab import CALENDAR_KINDS

PERIOD_STATUSES: tuple[str, ...] = ("open", "closed", "locked")


class AccountingPeriod(TenantOwned, SQLModel, table=True):
    """A ledger period. Nothing may post to a period that is not open.

    `status` replaces a boolean because "closed" and "locked" differ: a closed
    period can be reopened by a controller, a locked one cannot. The posting ban
    is checked by the reconciliation gate, which is what makes the flag mean
    something.
    """

    __tablename__ = "accounting_periods"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "name", name="uq_accounting_periods_name"),
        one_of("status", PERIOD_STATUSES, "accounting_periods"),
        CheckConstraint("end_date >= start_date", name="ck_accounting_periods_dates"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    name: str = Field(max_length=32)
    start_date: date
    end_date: date
    status: str = Field(default="open", max_length=16, index=True)
    cutoff: date | None = None


class BankHoliday(SQLModel, table=True):
    """Settlement calendar, by country. Not tenant-scoped — it is public fact.

    Kept separate from `CalendarEvent` because a holiday shifts settlement for
    every tenant in a jurisdiction, whereas a calendar event belongs to one
    company. Phase 3's cadence layer reads this to shift pay and AP dates.
    """

    __tablename__ = "bank_holidays"
    __table_args__ = (UniqueConstraint("country", "holiday_date", name="uq_bank_holidays_date"),)

    id: str = Field(default_factory=new_id, primary_key=True)
    country: str = Field(max_length=2, index=True)
    holiday_date: date = Field(index=True)
    name: str = Field(max_length=128)
    settlement_closed: bool = True


class CalendarEvent(TenantOwned, SQLModel, table=True):
    """A dated company obligation: pay run, tax deposit, AP run, rent, debt service."""

    __tablename__ = "calendar_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "kind", "event_date", "label", name="uq_calendar_events_slot"
        ),
        one_of("kind", CALENDAR_KINDS, "calendar_events"),
        money_pair("amount_minor", "currency", "calendar_events"),
        valid_currency("currency", "calendar_events"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    kind: str = Field(max_length=32, index=True)
    event_date: date = Field(index=True)
    label: str = Field(max_length=256)
    amount_minor: int | None = Field(default=None, sa_type=BigInteger)
    currency: str | None = Field(default=None, sa_type=CHAR(3), max_length=3)
    protected: bool = False
