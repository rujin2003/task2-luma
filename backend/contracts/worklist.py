from __future__ import annotations

from datetime import date
from typing import Literal

from backend.contracts.common import FrozenModel, MoneyDTO

WorklistStatus = Literal[
    "open",
    "queued",
    "needs_approval",
    "approved",
    "rejected",
    "done",
]


class WorklistItem(FrozenModel):
    owner: str
    action: str
    counterparty: str | None = None
    document_ref: str | None = None
    amount: MoneyDTO
    due_date: date
    status: WorklistStatus
    rejection_reason: str | None = None
    evidence_ref: str | None = None


class CollectionOpportunity(FrozenModel):
    invoice_ref: str
    customer: str
    amount: MoneyDTO
    days_past_due: int
    collection_probability_bps: int
    expected_accelerated: MoneyDTO
    action: str
    due: date


class DeferralCandidate(FrozenModel):
    vendor_invoice_ref: str
    vendor: str
    amount: MoneyDTO
    proposed_deferral_days: int
    early_pay_discount_cost: MoneyDTO
    criticality: str
    blocked: bool = False
    block_reason: str | None = None
    single_source: bool = False
    replacement_lead_time_days: int | None = None
