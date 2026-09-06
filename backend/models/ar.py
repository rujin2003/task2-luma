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
from backend.models.vocab import INVOICE_STATUSES, PAYMENT_CHANNELS, PAYMENT_METHODS


class Customer(TenantOwned, Sourced, SQLModel, table=True):
    __tablename__ = "customers"
    __table_args__ = (source_unique("customers"),)

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    name: str = Field(max_length=256)
    external_id: str | None = Field(default=None, max_length=64)
    segment: str | None = Field(default=None, max_length=64)


class Invoice(TenantOwned, Bitemporal, Sourced, SQLModel, table=True):
    """A trade receivable. `amount_minor` is a non-negative magnitude.

    `open_amount_minor` is a projection of `amount_minor` less applied payments;
    the reconciliation gate re-derives it from `PaymentApplication` rather than
    trusting it, because a hand-maintained balance is exactly the kind of number
    that drifts.
    """

    __tablename__ = "invoices"
    __table_args__ = (
        source_unique("invoices"),
        UniqueConstraint("tenant_id", "company_id", "invoice_ref", name="uq_invoices_ref"),
        non_negative("amount_minor", "invoices"),
        non_negative("open_amount_minor", "invoices"),
        valid_currency("currency", "invoices"),
        one_of("status", INVOICE_STATUSES, "invoices"),
        CheckConstraint("open_amount_minor <= amount_minor", name="ck_invoices_open_le_amount"),
        CheckConstraint("due_date >= issued_date", name="ck_invoices_due_after_issue"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    customer_id: str = Field(foreign_key="customers.id", index=True)
    invoice_ref: str = Field(max_length=64, index=True)
    issued_date: date
    due_date: date
    amount_minor: int = Field(sa_type=BigInteger)
    open_amount_minor: int = Field(sa_type=BigInteger)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    status: str = Field(default="open", max_length=32, index=True)
    disputed: bool = False
    promise_to_pay: date | None = None


class Payment(TenantOwned, Bitemporal, Sourced, SQLModel, table=True):
    """Cash received. A non-negative magnitude; direction is implied.

    Payments are *not* linked to a single invoice. Real receipts arrive short,
    over, on account, or spanning several invoices, so the link lives in
    `PaymentApplication`.
    """

    __tablename__ = "payments"
    __table_args__ = (
        source_unique("payments"),
        UniqueConstraint("tenant_id", "company_id", "payment_ref", name="uq_payments_ref"),
        non_negative("amount_minor", "payments"),
        valid_currency("currency", "payments"),
        one_of("method", PAYMENT_METHODS, "payments"),
        one_of("channel", PAYMENT_CHANNELS, "payments"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    subscription_id: str | None = Field(default=None, foreign_key="subscriptions.id")
    payment_ref: str = Field(max_length=64, index=True)
    amount_minor: int = Field(sa_type=BigInteger)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    paid_date: date = Field(index=True)
    method: str = Field(default="ach", max_length=32)
    channel: str = Field(default="bank", max_length=32)


class PaymentApplication(TenantOwned, Bitemporal, Sourced, SQLModel, table=True):
    """Cash applied from a payment to an invoice.

    Partial payments, short pays and one cheque covering three invoices are all
    ordinary; without this table none of them are representable and AR aging is
    a fiction. Unapplied cash is the payment amount less its applications.
    """

    __tablename__ = "payment_applications"
    __table_args__ = (
        source_unique("payment_applications"),
        UniqueConstraint("payment_id", "invoice_id", name="uq_payment_applications_pair"),
        valid_currency("currency", "payment_applications"),
        CheckConstraint("amount_minor > 0", name="ck_payment_applications_positive"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    payment_id: str = Field(foreign_key="payments.id", index=True)
    invoice_id: str = Field(foreign_key="invoices.id", index=True)
    amount_minor: int = Field(sa_type=BigInteger)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    applied_date: date
