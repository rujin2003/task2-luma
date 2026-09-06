"""Agent identity, findings, and the persisted record of a run.

The Agent Output Contract: agents return structured findings with evidence, never prose
and never chain-of-thought. A finding with `status == COMPLETE` must carry at least one
evidence reference, and the evidence validator must be able to resolve every one of them
before the finding is surfaced.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.contracts.confidence import Confidence
from backend.contracts.money import Money
from backend.contracts.provenance import Evidence


class AgentRole(StrEnum):
    """The six specialists plus the coordinating roles. Also the model-routing key."""

    FORECAST = "forecast"
    VARIANCE = "variance"
    AR_COLLECTIONS = "ar_collections"
    AP_OPTIMIZATION = "ap_optimization"
    SUPPLIER_RISK = "supplier_risk"
    DODO_REVENUE = "dodo_revenue"
    COMMANDER = "commander"
    CONFLICT_RESOLUTION = "conflict_resolution"
    STRESS_TEST = "stress_test"
    COVENANT_EXPLAINER = "covenant_explainer"
    CARTOGRAPHER = "cartographer"


class AgentStatus(StrEnum):
    """`QUEUED` is an honest status, not a failure -- a throttled wave still reports."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    DEGRADED = "degraded"
    REFUSED = "refused"
    TIMEOUT = "timeout"
    FAILED = "failed"


TERMINAL_STATUSES = frozenset(
    {
        AgentStatus.COMPLETE,
        AgentStatus.DEGRADED,
        AgentStatus.REFUSED,
        AgentStatus.TIMEOUT,
        AgentStatus.FAILED,
    }
)


class ActionKind(StrEnum):
    """Levers an agent may propose. Deterministic code prices and composes them."""

    COLLECTION_CALL = "collection_call"
    EARLY_PAY_DISCOUNT = "early_pay_discount"
    DISPUTE_RESOLUTION = "dispute_resolution"
    AP_DEFER = "ap_defer"
    AP_ACCELERATE = "ap_accelerate"
    DODO_RETRY = "dodo_retry"
    DODO_DUNNING = "dodo_dunning"
    REVOLVER_DRAW = "revolver_draw"
    ASSUMPTION_REVIEW = "assumption_review"


class ProposedAction(BaseModel):
    """A lever, as an agent proposes it -- not yet priced, sequenced or approved."""

    model_config = ConfigDict(frozen=True)

    kind: ActionKind
    rationale: Annotated[str, Field(min_length=1, max_length=400)]
    counterparty: str | None = None
    document_ref: str | None = None
    amount: Money | None = None
    due_by: date | None = None
    delay_days: Annotated[int, Field(ge=0, le=180)] | None = None
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _deferral_states_a_delay(self) -> Self:
        if self.kind is ActionKind.AP_DEFER and self.delay_days is None:
            raise ValueError("an AP deferral must state delay_days")
        return self


class AgentFinding(BaseModel):
    """What an agent returns. Structured, cited, and free of chain-of-thought."""

    model_config = ConfigDict(frozen=True)

    agent: AgentRole
    status: AgentStatus
    headline: Annotated[str, Field(min_length=1, max_length=200)]
    detail: Annotated[str, Field(max_length=1200)] = ""
    quantum: Money | None = None
    confidence: Confidence | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    risks: list[Annotated[str, Field(max_length=280)]] = Field(default_factory=list)
    recommended_actions: list[ProposedAction] = Field(default_factory=list)
    rejects: list[str] = Field(
        default_factory=list,
        description="Identifiers of other agents' proposed actions this finding rejects.",
    )
    requires_followup: bool = False
    followup_question: Annotated[str, Field(max_length=280)] | None = None

    @model_validator(mode="after")
    def _complete_findings_are_cited(self) -> Self:
        if self.status is AgentStatus.COMPLETE and not self.evidence:
            raise ValueError("a complete finding must cite at least one evidence reference")
        if self.requires_followup and not self.followup_question:
            raise ValueError("requires_followup implies a followup_question")
        if self.rejects and not self.evidence:
            raise ValueError("rejecting another agent's proposal requires attached evidence")
        return self

    def digest_line(self) -> str:
        """One line for the cross-agent findings digest. Conclusions only, never outputs."""
        mark = {
            AgentStatus.COMPLETE: "ok",
            AgentStatus.DEGRADED: "degraded",
            AgentStatus.REFUSED: "refused",
            AgentStatus.TIMEOUT: "timeout",
            AgentStatus.FAILED: "failed",
        }.get(self.status, self.status.value)
        parts = [f"{self.agent.value} {mark}", self.headline]
        if self.quantum is not None:
            parts.append(str(self.quantum))
        return " | ".join(parts)


class ToolCall(BaseModel):
    """One tool invocation, recorded for the audit trail and the token budget."""

    model_config = ConfigDict(frozen=True)

    tool: str
    arguments: dict[str, object] = Field(default_factory=dict)
    started_at: datetime
    duration_ms: int
    row_count: int | None = None
    truncated_from: int | None = None
    error: str | None = None


class TokenUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class AgentRun(BaseModel):
    """Persisted record of one agent invocation. Every field here is auditable."""

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    investigation_id: str | None = None
    agent: AgentRole
    status: AgentStatus
    model: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    latency_ms: int | None = None
    queued_ms: int | None = None
    usage: TokenUsage = TokenUsage()
    tool_calls: list[ToolCall] = Field(default_factory=list)
    context_pack: str | None = None
    finding: AgentFinding | None = None
    failure_reason: str | None = None
    attempts: Annotated[int, Field(ge=1)] = 1

    @model_validator(mode="after")
    def _terminal_runs_explain_themselves(self) -> Self:
        if self.status in {AgentStatus.FAILED, AgentStatus.TIMEOUT} and not self.failure_reason:
            raise ValueError("a failed or timed-out run must record a failure_reason")
        if self.status is AgentStatus.COMPLETE and self.finding is None:
            raise ValueError("a complete run must carry a finding")
        return self
