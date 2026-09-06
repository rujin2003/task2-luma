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
from backend.models.vocab import (
    PAYMENT_RUN_STATUSES,
    VENDOR_CRITICALITIES,
    VENDOR_INVOICE_STATUSES,
)


class Vendor(TenantOwned, Sourced, SQLModel, table=True):
    __tablename__ = "vendors"
    __table_args__ = (
        source_unique("vendors"),
        one_of("criticality", VENDOR_CRITICALITIES, "vendors"),
        non_negative("replacement_lead_time_days", "vendors"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    name: str = Field(max_length=256)
    criticality: str = Field(default="standard", max_length=32)
    single_source: bool = False
    replacement_lead_time_days: int | None = None
    protected_class: str | None = Field(default=None, max_length=32)


class VendorInvoice(TenantOwned, Bitemporal, Sourced, SQLModel, table=True):
    """A trade payable. `amount_minor` is a non-negative magnitude."""

    __tablename__ = "vendor_invoices"
    __table_args__ = (
        source_unique("vendor_invoices"),
        UniqueConstraint("tenant_id", "vendor_id", "invoice_ref", name="uq_vendor_invoices_ref"),
        non_negative("amount_minor", "vendor_invoices"),
        non_negative("open_amount_minor", "vendor_invoices"),
        non_negative("early_pay_discount_bps", "vendor_invoices"),
        valid_currency("currency", "vendor_invoices"),
        one_of("status", VENDOR_INVOICE_STATUSES, "vendor_invoices"),
        CheckConstraint(
            "open_amount_minor <= amount_minor", name="ck_vendor_invoices_open_le_amount"
        ),
        CheckConstraint("due_date >= issued_date", name="ck_vendor_invoices_due_after_issue"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    vendor_id: str = Field(foreign_key="vendors.id", index=True)
    invoice_ref: str = Field(max_length=64, index=True)
    issued_date: date
    due_date: date
    amount_minor: int = Field(sa_type=BigInteger)
    open_amount_minor: int = Field(sa_type=BigInteger)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    status: str = Field(default="open", max_length=32, index=True)
    payment_run_id: str | None = Field(default=None, foreign_key="payment_runs.id", index=True)
    early_pay_discount_bps: int = 0
    early_pay_days: int | None = None


class PaymentRun(TenantOwned, Bitemporal, Sourced, SQLModel, table=True):
    """A batch AP disbursement on a fixed day (WORKFLOW.md §3 — Thursday runs).

    `amount_minor` is a non-negative magnitude; the reconciliation gate checks it
    against the sum of the vendor invoices assigned to the run.
    """

    __tablename__ = "payment_runs"
    __table_args__ = (
        source_unique("payment_runs"),
        non_negative("amount_minor", "payment_runs"),
        valid_currency("currency", "payment_runs"),
        one_of("status", PAYMENT_RUN_STATUSES, "payment_runs"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    run_date: date = Field(index=True)
    amount_minor: int = Field(sa_type=BigInteger)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    status: str = Field(default="scheduled", max_length=32)
