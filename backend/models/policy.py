from __future__ import annotations

from datetime import datetime

from sqlalchemy import CHAR, BigInteger, CheckConstraint, UniqueConstraint
from sqlmodel import Field, SQLModel

from backend.models.base import TenantOwned, new_id, non_negative, valid_currency


class TreasuryPolicyRow(TenantOwned, SQLModel, table=True):
    """Persisted policy version. Code reads a row, never a hard-coded constant."""

    __tablename__ = "treasury_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "version", name="uq_treasury_policies_version"),
        non_negative("min_unrestricted_cash_minor", "treasury_policies"),
        non_negative("min_30d_liquidity_minor", "treasury_policies"),
        non_negative("materiality_absolute_minor", "treasury_policies"),
        non_negative("materiality_pct_opex_bps", "treasury_policies"),
        valid_currency("currency", "treasury_policies"),
        CheckConstraint("version >= 1", name="ck_treasury_policies_version_positive"),
        CheckConstraint(
            "max_revolver_utilization_bps BETWEEN 0 AND 10000",
            name="ck_treasury_policies_utilization_bps",
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    version: int
    effective_at: datetime
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    min_unrestricted_cash_minor: int = Field(sa_type=BigInteger)
    min_30d_liquidity_minor: int = Field(sa_type=BigInteger)
    max_revolver_utilization_bps: int
    protected_payment_classes: str
    materiality_absolute_minor: int = Field(sa_type=BigInteger)
    materiality_pct_opex_bps: int
    body_yaml: str
