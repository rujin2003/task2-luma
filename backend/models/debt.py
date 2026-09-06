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
    one_of,
    source_unique,
    valid_currency,
)
from backend.models.vocab import COVENANT_FREQUENCIES


class DebtFacility(TenantOwned, Bitemporal, Sourced, SQLModel, table=True):
    __tablename__ = "debt_facilities"
    __table_args__ = (
        source_unique("debt_facilities"),
        UniqueConstraint("tenant_id", "company_id", "name", name="uq_debt_facilities_name"),
        non_negative("limit_minor", "debt_facilities"),
        non_negative("drawn_minor", "debt_facilities"),
        non_negative("commitment_fee_bps", "debt_facilities"),
        valid_currency("currency", "debt_facilities"),
        CheckConstraint("drawn_minor <= limit_minor", name="ck_debt_facilities_drawn_le_limit"),
        CheckConstraint(
            "max_utilization_bps BETWEEN 0 AND 10000", name="ck_debt_facilities_utilization_bps"
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    name: str = Field(max_length=128)
    limit_minor: int = Field(sa_type=BigInteger)
    drawn_minor: int = Field(sa_type=BigInteger)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    base_rate_name: str = Field(default="SOFR", max_length=32)
    spread_bps: int = 0
    commitment_fee_bps: int = 0
    max_utilization_bps: int = 7000
    maturity: date


class DebtCovenant(TenantOwned, Sourced, SQLModel, table=True):
    """A covenant with its real test date and definition.

    A covenant is tested on a date against a defined ratio, not evaluated
    continuously; `next_test_date` and `definition` are what make Phase 3's
    covenant module honest.
    """

    __tablename__ = "debt_covenants"
    __table_args__ = (
        source_unique("debt_covenants"),
        UniqueConstraint("tenant_id", "facility_id", "name", name="uq_debt_covenants_name"),
        money_pair("threshold_minor", "currency", "debt_covenants"),
        valid_currency("currency", "debt_covenants"),
        one_of("test_frequency", COVENANT_FREQUENCIES, "debt_covenants"),
        CheckConstraint(
            "(threshold_bps IS NOT NULL) OR (threshold_minor IS NOT NULL)",
            name="ck_debt_covenants_has_threshold",
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    facility_id: str = Field(foreign_key="debt_facilities.id", index=True)
    name: str = Field(max_length=128)
    definition: str = Field(max_length=1024)
    threshold_bps: int | None = None
    threshold_minor: int | None = Field(default=None, sa_type=BigInteger)
    currency: str | None = Field(default=None, sa_type=CHAR(3), max_length=3)
    test_frequency: str = Field(default="quarterly", max_length=32)
    next_test_date: date = Field(index=True)
