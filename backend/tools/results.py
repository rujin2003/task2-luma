"""What a tool hands back to an agent.

Every payload here is a *decision*: pre-aggregated, pre-ranked, row-capped, with the
arithmetic already done. A tool never returns a table for the model to sum -- it will
sometimes get it wrong, and it costs tokens to be wrong.

Two conventions hold across every payload:

* `references` lists the citations the tool actually supports. The evidence validator
  rejects a finding that cites anything outside this set, which is how "never fabricate
  evidence" survives contact with a model that would happily invent a row id.
* `truncation` is explicit. "showing 10 of 847" is reported, never silent.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from backend.contracts.constraints import Constraint
from backend.contracts.money import Money
from backend.contracts.provenance import SourceSystem


class Truncation(BaseModel):
    """Explicit, reported truncation. A cap the agent cannot see is a lie by omission."""

    model_config = ConfigDict(frozen=True)

    showing: Annotated[int, Field(ge=0)]
    of: Annotated[int, Field(ge=0)]

    def display(self) -> str:
        return f"showing {self.showing} of {self.of}"


class ToolPayload(BaseModel):
    """Base for every tool result."""

    model_config = ConfigDict(frozen=True)

    references: list[str] = Field(default_factory=list)
    truncation: Truncation | None = None
    as_of: date | None = None


def cap[RowT: BaseModel](rows: list[RowT], limit: int) -> tuple[list[RowT], Truncation | None]:
    """Apply a hard row cap, reporting the cut rather than hiding it."""
    if len(rows) <= limit:
        return rows, None
    return rows[:limit], Truncation(showing=limit, of=len(rows))


# --- shared position -----------------------------------------------------------------


class LiquidityPosition(ToolPayload):
    cash_today: Money
    floor: Money
    min_cash: Money
    min_cash_week: Annotated[int, Field(ge=1, le=13)]
    revolver_available: Money
    revolver_utilization_pct: Decimal
    runway_weeks: Annotated[int, Field(ge=0)]

    @property
    def breaches_floor(self) -> bool:
        return self.min_cash < self.floor


class CovenantRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    covenant_id: str
    label: str
    observed_ratio: Decimal
    threshold_ratio: Decimal
    headroom_pct: Decimal
    breached: bool
    tested_on: date
    reference: str


class CovenantStatus(ToolPayload):
    """An agent may explain a covenant. It may never compute one."""

    covenants: list[CovenantRow] = Field(default_factory=list)


class PolicyConstraints(ToolPayload):
    constraints: list[Constraint] = Field(default_factory=list)


class CapabilityManifest(ToolPayload):
    """Which sources this tenant actually has. The Commander plans against this.

    A tenant with no Dodo volume must not have a Dodo agent invoked, and the plan has to
    be able to say so.
    """

    available_sources: list[SourceSystem] = Field(default_factory=list)
    missing_sources: list[SourceSystem] = Field(default_factory=list)
    notes: dict[str, str] = Field(default_factory=dict)


# --- forecast and variance -----------------------------------------------------------


class ForecastWeekRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    week_index: Annotated[int, Field(ge=1, le=13)]
    week_ending: date
    closing_cash: Money
    breaches_floor: bool


class ForecastSummary(ToolPayload):
    version_id: str
    published: bool
    weeks: list[ForecastWeekRow] = Field(default_factory=list)


class AssumptionRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    category: str
    driver: str
    value_display: str
    last_refreshed: date
    days_since_refresh: Annotated[int, Field(ge=0)]
    stale: bool
    reference: str


class DriverAssumptions(ToolPayload):
    """The Forecast Agent explains these and flags the stale ones. It does not set them."""

    rows: list[AssumptionRow] = Field(default_factory=list)


class VarianceRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    category: str
    plan: Money
    actual: Money
    delta: Money
    material: bool
    reference: str


class VarianceBridge(ToolPayload):
    week_ending: date
    rows: list[VarianceRow] = Field(default_factory=list)
    total_delta: Money


class ErrorPercentile(BaseModel):
    model_config = ConfigDict(frozen=True)

    horizon_weeks: Annotated[int, Field(ge=1, le=13)]
    category: str
    p50_pct: Decimal
    p90_pct: Decimal
    sample_size: Annotated[int, Field(ge=1)]


class ForecastErrorPercentiles(ToolPayload):
    """Calibration for the stress test: our own measured error, not an arbitrary -10%."""

    percentiles: list[ErrorPercentile] = Field(default_factory=list)


# --- receivables ---------------------------------------------------------------------


class CollectionRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    customer: str
    document_ref: str
    amount: Money
    due_date: date
    days_past_due: int
    probability_pct: Annotated[Decimal, Field(ge=0, le=100)]
    expected_amount: Money
    empirical_basis: str
    reference: str


class CollectionOpportunities(ToolPayload):
    """Ranked rows, probability-weighted. Never assume all open AR is collectible."""

    rows: list[CollectionRow] = Field(default_factory=list)
    total_open: Money
    total_expected: Money


class AgingBucket(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    amount: Money
    invoice_count: Annotated[int, Field(ge=0)]


class ArAgingSummary(ToolPayload):
    buckets: list[AgingBucket] = Field(default_factory=list)
    total: Money


# --- payables and supplier risk ------------------------------------------------------


class DeferralRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    supplier: str
    document_ref: str
    amount: Money
    due_date: date
    max_delay_days: Annotated[int, Field(ge=0)]
    payment_class: str
    protected: bool
    discount_forgone: Money | None = None
    reference: str


class DeferralCandidates(ToolPayload):
    """Protected classes are marked here and blocked upstream by policy, not by prompt."""

    rows: list[DeferralRow] = Field(default_factory=list)
    total_deferrable: Money


class SupplierRiskProfile(ToolPayload):
    supplier: str
    sole_source: bool
    concentration_pct: Annotated[Decimal, Field(ge=0, le=100)]
    late_payments_6m: Annotated[int, Field(ge=0)]
    open_disputes: Annotated[int, Field(ge=0)]
    contractual_terms_days: Annotated[int, Field(ge=0)]
    on_credit_hold: bool
    notes: str = ""


# --- dodo ----------------------------------------------------------------------------


class DeclineRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    kind: str  # "soft" or "hard", per Dodo's documented taxonomy
    count: Annotated[int, Field(ge=0)]
    amount: Money
    recoverable_amount: Money
    retry_window_days: Annotated[int, Field(ge=0)] | None = None
    reference: str


class DodoDeclineBreakdown(ToolPayload):
    """Recovery follows Dodo's documented soft/hard taxonomy, not an invented rate."""

    rows: list[DeclineRow] = Field(default_factory=list)
    at_risk_total: Money
    recoverable_total: Money


# --- evidence ------------------------------------------------------------------------


class EvidenceRow(ToolPayload):
    """One resolved source row. The Evidence Explorer walks provenance down to this."""

    reference: str
    source: SourceSystem
    excerpt: str
    fields: dict[str, str] = Field(default_factory=dict)
    resolved: bool = True
