from __future__ import annotations

from sqlalchemy import CHAR, CheckConstraint, UniqueConstraint
from sqlmodel import Field, SQLModel

from backend.models.base import TenantOwned, new_id, valid_currency


class Company(TenantOwned, SQLModel, table=True):
    """The reporting entity.

    `business_timezone` is stored rather than assumed: pay dates, AP run days and
    week buckets are calendar dates in the company's timezone, and Phase 3's
    cadence layer is wrong by up to a day without it. Weeks are ISO weeks
    starting Monday everywhere in this system.
    """

    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_companies_name"),
        valid_currency("currency", "companies"),
        CheckConstraint("fiscal_year_end_month BETWEEN 1 AND 12", name="ck_companies_fiscal_month"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    name: str = Field(max_length=256)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    legal_name: str | None = Field(default=None, max_length=256)
    country: str = Field(default="US", max_length=2)
    business_timezone: str = Field(default="America/New_York", max_length=64)
    fiscal_year_end_month: int = 12
