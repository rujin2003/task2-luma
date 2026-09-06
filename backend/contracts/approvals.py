"""Approvals, overrides and the maker-checker rule.

An approval card renders exactly eight fields, in a fixed order, because that is what a
treasurer expects to read before signing. The card is a contract, not a layout choice.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.contracts.money import Money
from backend.contracts.provenance import Evidence

# The approval card, in render order. The UI must not reorder or omit these.
APPROVAL_CARD_FIELDS: tuple[str, ...] = (
    "ACTION",
    "AMOUNT",
    "EXPECTED IMPACT",
    "RISK",
    "EVIDENCE",
    "WHY RECOMMENDED",
    "WHAT COULD GO WRONG",
    "APPROVAL REQUIRED",
)


class ApprovalRole(StrEnum):
    ANALYST = "analyst"
    TREASURER = "treasurer"
    CFO = "cfo"
    BOARD = "board"


class ApprovalState(StrEnum):
    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    BLOCKED = "blocked"  # a policy constraint forbids the action outright
    EXPIRED = "expired"


class ApprovalRoute(BaseModel):
    """A delegation-of-authority band: who prepares, who reviews, who approves."""

    model_config = ConfigDict(frozen=True)

    route_id: Annotated[str, Field(min_length=1)]
    action_class: Annotated[str, Field(min_length=1)]
    lower_bound: Money | None = None
    upper_bound: Money | None = None
    responsible: ApprovalRole
    reviewer: ApprovalRole | None = None
    accountable: ApprovalRole | None = None
    informed: list[ApprovalRole] = Field(default_factory=list)
    blocked: bool = False
    blocked_reason: str | None = None

    @model_validator(mode="after")
    def _blocked_routes_explain_themselves(self) -> Self:
        if self.blocked and not self.blocked_reason:
            raise ValueError("a blocked route must state the policy constraint that blocks it")
        if not self.blocked and self.accountable is None:
            raise ValueError("a non-blocked route must name an accountable approver")
        if (
            self.lower_bound is not None
            and self.upper_bound is not None
            and self.upper_bound < self.lower_bound
        ):
            raise ValueError("upper_bound must not be below lower_bound")
        return self


class ApprovalRequest(BaseModel):
    """The card. Never execute a consequential action without one of these approved."""

    model_config = ConfigDict(frozen=True)

    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    worklist_seq: Annotated[int, Field(ge=1)] | None = None
    recommendation_id: str | None = None

    action: Annotated[str, Field(min_length=1, max_length=200)]
    amount: Money
    expected_impact: Annotated[str, Field(min_length=1, max_length=400)]
    risk: Annotated[str, Field(min_length=1, max_length=400)]
    evidence: list[Evidence] = Field(min_length=1)
    why_recommended: Annotated[str, Field(min_length=1, max_length=600)]
    what_could_go_wrong: Annotated[str, Field(min_length=1, max_length=600)]
    approval_required: ApprovalRole

    route: ApprovalRoute | None = None
    prepared_by: Annotated[str, Field(min_length=1)]
    state: ApprovalState = ApprovalState.PENDING
    created_at: datetime
    data_snapshot_ref: str | None = Field(
        default=None, description="Which data snapshot the approver was shown."
    )

    def card(self) -> dict[str, str]:
        """Render in the fixed field order. Keys are the contract."""
        return {
            "ACTION": self.action,
            "AMOUNT": str(self.amount),
            "EXPECTED IMPACT": self.expected_impact,
            "RISK": self.risk,
            "EVIDENCE": "; ".join(e.reference for e in self.evidence),
            "WHY RECOMMENDED": self.why_recommended,
            "WHAT COULD GO WRONG": self.what_could_go_wrong,
            "APPROVAL REQUIRED": self.approval_required.value,
        }


class ApprovalDecision(BaseModel):
    """Who decided, when, on what they saw. Maker-checker is enforced here."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    decided_by: Annotated[str, Field(min_length=1)]
    decided_by_role: ApprovalRole
    approved: bool
    reason: Annotated[str, Field(max_length=600)] = ""
    decided_at: datetime
    data_snapshot_ref: str | None = None

    @model_validator(mode="after")
    def _rejection_states_a_reason(self) -> Self:
        if not self.approved and not self.reason:
            raise ValueError("a rejection must state a reason")
        return self


class SegregationOfDutiesError(ValueError):
    """Raised when the preparer of a request tries to approve it."""


def check_maker_checker(request: ApprovalRequest, decision: ApprovalDecision) -> None:
    """Preparer is not approver. Enforced in code, not convention -- this is SOX-relevant."""
    if decision.request_id != request.request_id:
        raise ValueError("decision does not belong to this request")
    if request.state is ApprovalState.BLOCKED:
        raise SegregationOfDutiesError("this action is blocked by policy and cannot be approved")
    if decision.decided_by.strip().lower() == request.prepared_by.strip().lower():
        raise SegregationOfDutiesError(
            f"{decision.decided_by} prepared this request and cannot also approve it"
        )


class Override(BaseModel):
    """A human replacing a generated assumption, with a stated reason.

    An overridden assumption is better data than a generated one, so this is a
    first-class recorded object that feeds back into accuracy tracking -- not an edit.
    """

    model_config = ConfigDict(frozen=True)

    override_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    forecast_version_id: str | None = None
    target_ref: Annotated[str, Field(min_length=1)]
    field: Annotated[str, Field(min_length=1)]
    previous_value: str
    new_value: str
    previous_amount: Money | None = None
    new_amount: Money | None = None
    reason: Annotated[str, Field(min_length=1, max_length=600)]
    author: Annotated[str, Field(min_length=1)]
    author_role: ApprovalRole
    created_at: datetime

    @model_validator(mode="after")
    def _override_changes_something(self) -> Self:
        if self.previous_value == self.new_value and self.previous_amount == self.new_amount:
            raise ValueError("an override must change the value it targets")
        return self
