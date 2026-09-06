"""Closed vocabularies for status and kind columns.

Free-text status columns are how a data model quietly stops being able to make
inconsistent data unrepresentable: nothing rejects `'Open'`, `'OPEN'` or
`'opne'`, and every consumer grows its own defensive normalisation. These tuples
are the single definition, rendered into CHECK constraints by
`backend.models.base.one_of` and reused by the seeder.
"""

from __future__ import annotations

# Categories are frozen by WORKFLOW.md §3. A forecast whose categories move
# cannot be variance-analysed against its own history, so this is a closed set.
FORECAST_CATEGORIES: tuple[str, ...] = (
    "receipts_trade_ar",
    "receipts_subscription",
    "receipts_other",
    "payroll_salaried",
    "payroll_hourly",
    "payroll_taxes_benefits",
    "ap_trade",
    "rent_leases",
    "tax_income_estimated",
    "tax_sales_vat",
    "debt_interest",
    "debt_principal",
    "capex",
    "insurance_software",
)

INVOICE_STATUSES: tuple[str, ...] = ("open", "part_paid", "paid", "written_off", "void")
VENDOR_INVOICE_STATUSES: tuple[str, ...] = ("open", "part_paid", "paid", "disputed", "void")
PAYMENT_RUN_STATUSES: tuple[str, ...] = (
    "scheduled",
    "approved",
    "released",
    "settled",
    "cancelled",
)
PAYMENT_METHODS: tuple[str, ...] = ("ach", "wire", "card", "check", "sepa", "internal")
PAYMENT_CHANNELS: tuple[str, ...] = ("bank", "dodo", "manual")
SUBSCRIPTION_INTERVALS: tuple[str, ...] = ("monthly", "quarterly", "annual")
SUBSCRIPTION_STATUSES: tuple[str, ...] = ("active", "past_due", "paused", "cancelled")
CALENDAR_KINDS: tuple[str, ...] = (
    "payroll",
    "payroll_tax",
    "ap_run",
    "rent",
    "tax_estimated",
    "tax_sales",
    "debt_service",
    "bank_holiday",
    "other",
)
VARIANCE_KINDS: tuple[str, ...] = ("actual_vs_forecast", "forecast_vs_prior")
WORKLIST_STATUSES: tuple[str, ...] = (
    "open",
    "prepared",
    "in_review",
    "approved",
    "rejected",
    "done",
)
APPROVAL_DECISIONS: tuple[str, ...] = ("pending", "approved", "rejected", "escalated")
APPROVER_ROLES: tuple[str, ...] = ("analyst", "treasurer", "cfo", "board")
RECOMMENDATION_STATUSES: tuple[str, ...] = ("draft", "proposed", "accepted", "rejected", "expired")
AGENT_RUN_STATUSES: tuple[str, ...] = ("running", "ok", "failed", "timeout", "degraded")
COVENANT_FREQUENCIES: tuple[str, ...] = ("monthly", "quarterly", "semiannual", "annual")
VENDOR_CRITICALITIES: tuple[str, ...] = ("standard", "important", "critical")

# Event types on the append-only ledger. Entity tables are projections of these.
EVENT_TYPES: tuple[str, ...] = (
    "company_registered",
    "customer_registered",
    "vendor_registered",
    "bank_account_opened",
    "bank_transaction_posted",
    "gl_account_opened",
    "journal_posted",
    "invoice_issued",
    "invoice_payment_applied",
    "vendor_invoice_received",
    "payment_received",
    "payment_run_scheduled",
    "subscription_started",
    "forecast_published",
    "override_applied",
)

__all__ = [
    "AGENT_RUN_STATUSES",
    "APPROVAL_DECISIONS",
    "APPROVER_ROLES",
    "CALENDAR_KINDS",
    "COVENANT_FREQUENCIES",
    "EVENT_TYPES",
    "FORECAST_CATEGORIES",
    "INVOICE_STATUSES",
    "PAYMENT_CHANNELS",
    "PAYMENT_METHODS",
    "PAYMENT_RUN_STATUSES",
    "RECOMMENDATION_STATUSES",
    "SUBSCRIPTION_INTERVALS",
    "SUBSCRIPTION_STATUSES",
    "VARIANCE_KINDS",
    "VENDOR_CRITICALITIES",
    "VENDOR_INVOICE_STATUSES",
    "WORKLIST_STATUSES",
]
