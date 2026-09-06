"""Strategies, worklists, stress results and the recommendation.

The output of the system is a worklist -- rows with an owner, a counterparty, a document
reference, an amount, a date and a status. "Accelerate $2.6M AR" is not something anyone
can approve or execute; row 1 of a worklist is.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.contracts.agent import AgentRole, ProposedAction
from backend.contracts.constraints import ConstraintSeverity, ConstraintViolation
from backend.contracts.money import Money
from backend.contracts.provenance import Evidence


class WorklistStatus(StrEnum):
    OPEN = "open"
    QUEUED = "queued"
    NEEDS_APPROVAL = "needs_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    LANDED = "landed"
    MISSED = "missed"


class WorklistItem(BaseModel):
    """One executable row. Every field is what a human needs to actually do the thing."""

    model_config = ConfigDict(frozen=True)

    seq: Annotated[int, Field(ge=1)]
    owner: Annotated[str, Field(min_length=1)]
    action: Annotated[str, Field(min_length=1, max_length=200)]
    counterparty: str | None = None
    document_ref: str | None = None
    amount: Money
    due_date: date
    status: WorklistStatus = WorklistStatus.OPEN
    proposed_by: AgentRole | None = None
    expected_cash_impact: Money | None = None
    probability_pct: Annotated[Decimal, Field(ge=0, le=100)] | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    approval_request_id: str | None = None

    @model_validator(mode="after")
    def _approval_rows_reference_a_request(self) -> Self:
        if self.status is WorklistStatus.NEEDS_APPROVAL and self.approval_request_id is None:
            raise ValueError("a row needing approval must reference an approval request")
        return self


class RejectedAction(BaseModel):
    """What we did *not* recommend, and why. A treasurer looks for this first."""

    model_config = ConfigDict(frozen=True)

    action: ProposedAction
    rejected_by: str
    reason: Annotated[str, Field(min_length=1, max_length=400)]
    violation: ConstraintViolation | None = None
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _rejection_is_evidenced(self) -> Self:
        if not self.evidence and self.violation is None:
            raise ValueError("a rejection must attach evidence or cite a constraint violation")
        return self


class Strategy(BaseModel):
    """A candidate bundle of levers. The scenario step generates several, not one."""

    model_config = ConfigDict(frozen=True)

    strategy_id: Annotated[str, Field(min_length=1)]
    name: Annotated[str, Field(min_length=1, max_length=120)]
    actions: list[ProposedAction] = Field(min_length=1)
    projected_min_cash: Money | None = None
    projected_min_cash_week: Annotated[int, Field(ge=1, le=13)] | None = None
    net_cash_impact: Money | None = None
    financing_cost: Money | None = None
    constraint_violations: list[ConstraintViolation] = Field(default_factory=list)

    @property
    def feasible(self) -> bool:
        return not any(v.severity is ConstraintSeverity.HARD for v in self.constraint_violations)


class Stressor(BaseModel):
    """A calibrated adverse condition. Selection is agentic; the math is not."""

    model_config = ConfigDict(frozen=True)

    stressor_id: Annotated[str, Field(min_length=1)]
    label: Annotated[str, Field(min_length=1, max_length=120)]
    category: str
    shift_pct: Decimal
    calibration: Annotated[str, Field(max_length=280)] = ""


class StressResult(BaseModel):
    """Outcome of one stressor against one strategy."""

    model_config = ConfigDict(frozen=True)

    strategy_id: str
    stressor: Stressor
    min_cash: Money
    min_cash_week: Annotated[int, Field(ge=1, le=13)]
    floor: Money
    passed: bool
    headroom: Money

    @model_validator(mode="after")
    def _passed_matches_headroom(self) -> Self:
        if self.passed != (self.min_cash >= self.floor):
            raise ValueError("passed must equal (min_cash >= floor)")
        if self.headroom != self.min_cash - self.floor:
            raise ValueError("headroom must equal min_cash - floor")
        return self


class ReplanAttempt(BaseModel):
    """Attempt history is preserved: the failure reason is what drove the next plan."""

    model_config = ConfigDict(frozen=True)

    attempt: Annotated[int, Field(ge=1)]
    strategy_id: str
    failure_reason: Annotated[str, Field(min_length=1, max_length=400)]
    tightened_constraint: str | None = None


class Recommendation(BaseModel):
    """The evidence-backed answer: selected strategy, worklist, and what was refused."""

    model_config = ConfigDict(frozen=True)

    recommendation_id: Annotated[str, Field(min_length=1)]
    investigation_id: str | None = None
    selected_strategy: Strategy
    alternatives: list[Strategy] = Field(default_factory=list)
    worklist: list[WorklistItem] = Field(default_factory=list)
    rejected_actions: list[RejectedAction] = Field(default_factory=list)
    stress_results: list[StressResult] = Field(default_factory=list)
    replan_history: list[ReplanAttempt] = Field(default_factory=list)
    degraded: bool = False
    degradation_reason: str | None = None
    requires_human_review: bool = False
    summary: Annotated[str, Field(max_length=1000)] = ""

    @model_validator(mode="after")
    def _degradation_is_explained(self) -> Self:
        if self.degraded and not self.degradation_reason:
            raise ValueError("a degraded recommendation must state why")
        if self.degraded and not self.requires_human_review:
            raise ValueError("a degraded recommendation always requires human review")
        return self
