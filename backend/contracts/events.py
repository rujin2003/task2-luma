"""The live event stream schema. **Frozen** -- the UI is built against this.

Every event carries a renderable envelope: a monotonic `seq`, a timestamp, a `mark` and a
one-line `status_line`. A client that understands nothing else can still render the War
Room activity feed correctly from those four fields alone.

Two rules are structural rather than advisory:

* **No chain-of-thought.** There is no field anywhere in this union for a model's
  reasoning. Events carry findings, evidence, decisions and status -- nothing else.
* **Additive change only.** Adding an event type or an optional field is a minor version
  bump. Renaming or removing anything is a break, and `tests/unit/test_events.py` fails
  the build when it happens.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from backend.contracts.agent import AgentFinding, AgentRole, AgentStatus
from backend.contracts.approvals import ApprovalDecision, ApprovalRequest
from backend.contracts.constraints import ConstraintViolation
from backend.contracts.money import Money
from backend.contracts.provenance import Evidence
from backend.contracts.strategy import Recommendation, ReplanAttempt, Strategy, StressResult

EVENT_SCHEMA_VERSION = "1.0.0"


class EventType(StrEnum):
    """The frozen vocabulary. The wire `event:` field is exactly this value."""

    STREAM_OPENED = "stream.opened"
    STREAM_HEARTBEAT = "stream.heartbeat"
    SYSTEM_DEGRADED = "system.degraded"

    INVESTIGATION_OPENED = "investigation.opened"
    INVESTIGATION_PHASE = "investigation.phase"
    INVESTIGATION_CLOSED = "investigation.closed"
    PLAN_SELECTED = "plan.selected"

    AGENT_QUEUED = "agent.queued"
    AGENT_STARTED = "agent.started"
    AGENT_TOOL_CALL = "agent.tool_call"
    AGENT_FINDING = "agent.finding"
    AGENT_STATUS = "agent.status"
    EVIDENCE_REJECTED = "evidence.rejected"

    CONFLICT_DETECTED = "conflict.detected"
    FOLLOWUP_DISPATCHED = "conflict.followup"
    CONFLICT_RESOLVED = "conflict.resolved"

    SCENARIO_GENERATED = "scenario.generated"
    STRESS_COMPLETED = "stress.completed"
    REPLAN_STARTED = "replan.started"
    RECOMMENDATION_READY = "recommendation.ready"

    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_DECIDED = "approval.decided"

    CYCLE_STEP = "cycle.step"


class StatusMark(StrEnum):
    """The glyph the activity feed shows. Matches the Agent UX section of the spec."""

    OK = "ok"
    WARN = "warn"
    WORKING = "working"
    FAIL = "fail"
    INFO = "info"


MARK_GLYPH: dict[StatusMark, str] = {
    StatusMark.OK: "✓",
    StatusMark.WARN: "⚠",
    StatusMark.WORKING: "↻",
    StatusMark.FAIL: "✗",
    StatusMark.INFO: "•",
}

STATUS_TO_MARK: dict[AgentStatus, StatusMark] = {
    AgentStatus.QUEUED: StatusMark.INFO,
    AgentStatus.RUNNING: StatusMark.WORKING,
    AgentStatus.COMPLETE: StatusMark.OK,
    AgentStatus.DEGRADED: StatusMark.WARN,
    AgentStatus.REFUSED: StatusMark.WARN,
    AgentStatus.TIMEOUT: StatusMark.FAIL,
    AgentStatus.FAILED: StatusMark.FAIL,
}


class InvestigationPhase(StrEnum):
    """The investigation state machine, as the UI needs to see it."""

    PLANNING = "planning"
    INVESTIGATING = "investigating"
    RESOLVING_CONFLICTS = "resolving_conflicts"
    GENERATING_SCENARIOS = "generating_scenarios"
    STRESS_TESTING = "stress_testing"
    REPLANNING = "replanning"
    RECOMMENDING = "recommending"
    AWAITING_APPROVAL = "awaiting_approval"
    CLOSED = "closed"
    FAILED = "failed"


class ConflictKind(StrEnum):
    """Detection is deterministic; only resolution is agentic."""

    STRUCTURAL = "structural"  # two agents disagree numerically beyond tolerance
    SEMANTIC = "semantic"  # AP says defer, Supplier Risk says do not


ShortText = Annotated[str, Field(min_length=1, max_length=200)]


class _Event(BaseModel):
    """The envelope. Present on every event, whatever its payload."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = EVENT_SCHEMA_VERSION
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    seq: Annotated[int, Field(ge=0)]
    ts: datetime
    investigation_id: str | None = None
    mark: StatusMark = StatusMark.INFO
    status_line: ShortText
    type: EventType  # each subclass narrows this to a single Literal -- the discriminator

    def to_sse(self) -> str:
        """Encode as a `text/event-stream` frame. `id:` is the resume cursor."""
        payload = self.model_dump_json(exclude_none=True)
        return f"id: {self.seq}\nevent: {self.type.value}\ndata: {payload}\n\n"

    def display(self) -> str:
        return f"{MARK_GLYPH[self.mark]} {self.status_line}"


# --- stream lifecycle ---------------------------------------------------------------


class StreamOpened(_Event):
    type: Literal[EventType.STREAM_OPENED] = EventType.STREAM_OPENED
    replay_from: Annotated[int, Field(ge=0)] = 0
    synthetic_data: bool = True


class StreamHeartbeat(_Event):
    """Keeps proxies from closing an idle stream. Carries no state."""

    type: Literal[EventType.STREAM_HEARTBEAT] = EventType.STREAM_HEARTBEAT


class SystemDegraded(_Event):
    """A degraded state is first-class UI, not an error toast."""

    type: Literal[EventType.SYSTEM_DEGRADED] = EventType.SYSTEM_DEGRADED
    component: ShortText
    reason: Annotated[str, Field(min_length=1, max_length=400)]
    mark: StatusMark = StatusMark.WARN


# --- investigation ------------------------------------------------------------------


class InvestigationOpened(_Event):
    """A war room opens on a specific, dated, quantified condition -- never a vibe."""

    type: Literal[EventType.INVESTIGATION_OPENED] = EventType.INVESTIGATION_OPENED
    trigger: ShortText
    detected_at: datetime
    quantum: Money | None = None
    breach: ConstraintViolation | None = None
    mark: StatusMark = StatusMark.WARN


class InvestigationPhaseChanged(_Event):
    type: Literal[EventType.INVESTIGATION_PHASE] = EventType.INVESTIGATION_PHASE
    phase: InvestigationPhase
    depth: Annotated[int, Field(ge=0)] = 0
    elapsed_ms: Annotated[int, Field(ge=0)] | None = None


class InvestigationClosed(_Event):
    type: Literal[EventType.INVESTIGATION_CLOSED] = EventType.INVESTIGATION_CLOSED
    phase: Literal[InvestigationPhase.CLOSED, InvestigationPhase.FAILED]
    recommendation_id: str | None = None
    reason: Annotated[str, Field(max_length=400)] = ""

    @model_validator(mode="after")
    def _failure_is_explained(self) -> Self:
        if self.phase is InvestigationPhase.FAILED and not self.reason:
            raise ValueError("a failed investigation must state why it failed")
        return self


class SkippedAgent(BaseModel):
    """Why an agent was *not* called. A pure-AR incident must not invoke Dodo."""

    model_config = ConfigDict(frozen=True)

    agent: AgentRole
    reason: Annotated[str, Field(min_length=1, max_length=280)]


class PlanSelected(_Event):
    """The Commander picks from an enumerated set of plans and justifies the shape."""

    type: Literal[EventType.PLAN_SELECTED] = EventType.PLAN_SELECTED
    plan_id: Annotated[str, Field(min_length=1)]
    invoked: list[AgentRole] = Field(min_length=1)
    skipped: list[SkippedAgent] = Field(default_factory=list)


# --- agents -------------------------------------------------------------------------


class AgentQueued(_Event):
    """`queued` is an honest status: a free tier throttles a nine-agent wave."""

    type: Literal[EventType.AGENT_QUEUED] = EventType.AGENT_QUEUED
    agent: AgentRole
    run_id: str
    queue_position: Annotated[int, Field(ge=0)] | None = None


class AgentStarted(_Event):
    type: Literal[EventType.AGENT_STARTED] = EventType.AGENT_STARTED
    agent: AgentRole
    run_id: str
    model: str | None = None
    mark: StatusMark = StatusMark.WORKING


class AgentToolCall(_Event):
    """Tool calls are shown; tool *arguments* are not, and neither is reasoning."""

    type: Literal[EventType.AGENT_TOOL_CALL] = EventType.AGENT_TOOL_CALL
    agent: AgentRole
    run_id: str
    tool: ShortText
    duration_ms: Annotated[int, Field(ge=0)] | None = None
    row_count: Annotated[int, Field(ge=0)] | None = None
    truncated_from: Annotated[int, Field(ge=0)] | None = None
    error: Annotated[str, Field(max_length=280)] | None = None
    mark: StatusMark = StatusMark.WORKING


class AgentFindingEmitted(_Event):
    """Emitted only after the evidence validator has resolved every reference."""

    type: Literal[EventType.AGENT_FINDING] = EventType.AGENT_FINDING
    agent: AgentRole
    run_id: str
    finding: AgentFinding

    @model_validator(mode="after")
    def _finding_belongs_to_the_named_agent(self) -> Self:
        if self.finding.agent is not self.agent:
            raise ValueError("finding.agent must match the event's agent")
        return self


class AgentStatusChanged(_Event):
    """Terminal status, including the honest failures: timeout, refusal, degradation."""

    type: Literal[EventType.AGENT_STATUS] = EventType.AGENT_STATUS
    agent: AgentRole
    run_id: str
    status: AgentStatus
    failure_reason: Annotated[str, Field(max_length=400)] | None = None
    latency_ms: Annotated[int, Field(ge=0)] | None = None
    attempt: Annotated[int, Field(ge=1)] = 1

    @model_validator(mode="after")
    def _failures_explain_themselves(self) -> Self:
        if self.status in {AgentStatus.FAILED, AgentStatus.TIMEOUT} and not self.failure_reason:
            raise ValueError("a failed or timed-out status event must carry a failure_reason")
        return self


class EvidenceRejected(_Event):
    """An unresolvable reference means the finding is rejected, not surfaced."""

    type: Literal[EventType.EVIDENCE_REJECTED] = EventType.EVIDENCE_REJECTED
    agent: AgentRole
    run_id: str
    reference: Annotated[str, Field(min_length=1)]
    reason: Annotated[str, Field(min_length=1, max_length=280)]
    mark: StatusMark = StatusMark.FAIL


# --- conflict -----------------------------------------------------------------------


class ConflictDetected(_Event):
    type: Literal[EventType.CONFLICT_DETECTED] = EventType.CONFLICT_DETECTED
    conflict_id: Annotated[str, Field(min_length=1)]
    kind: ConflictKind
    agents: list[AgentRole] = Field(min_length=2)
    description: Annotated[str, Field(min_length=1, max_length=400)]
    delta: Money | None = None
    mark: StatusMark = StatusMark.WARN


class FollowupDispatched(_Event):
    """Resolution is a scoped follow-up, not 'pick the higher confidence'."""

    type: Literal[EventType.FOLLOWUP_DISPATCHED] = EventType.FOLLOWUP_DISPATCHED
    conflict_id: str
    agent: AgentRole
    question: Annotated[str, Field(min_length=1, max_length=280)]
    mark: StatusMark = StatusMark.WORKING


class ConflictResolved(_Event):
    type: Literal[EventType.CONFLICT_RESOLVED] = EventType.CONFLICT_RESOLVED
    conflict_id: str
    resolution: Annotated[str, Field(min_length=1, max_length=400)]
    upheld: AgentRole | None = None
    evidence: list[Evidence] = Field(min_length=1)
    mark: StatusMark = StatusMark.OK


# --- strategy, stress, recommendation -----------------------------------------------


class ScenarioGenerated(_Event):
    type: Literal[EventType.SCENARIO_GENERATED] = EventType.SCENARIO_GENERATED
    strategy: Strategy


class StressCompleted(_Event):
    type: Literal[EventType.STRESS_COMPLETED] = EventType.STRESS_COMPLETED
    result: StressResult

    @model_validator(mode="after")
    def _mark_matches_outcome(self) -> Self:
        expected = StatusMark.OK if self.result.passed else StatusMark.WARN
        if self.mark is not expected:
            raise ValueError("a failed stress result must be marked WARN, a passing one OK")
        return self


class ReplanStarted(_Event):
    """The failure reason feeds the next plan; attempt history is preserved."""

    type: Literal[EventType.REPLAN_STARTED] = EventType.REPLAN_STARTED
    attempt: ReplanAttempt
    mark: StatusMark = StatusMark.WORKING


class RecommendationReady(_Event):
    type: Literal[EventType.RECOMMENDATION_READY] = EventType.RECOMMENDATION_READY
    recommendation: Recommendation


# --- approvals and the weekly cycle -------------------------------------------------


class ApprovalRequested(_Event):
    type: Literal[EventType.APPROVAL_REQUESTED] = EventType.APPROVAL_REQUESTED
    request: ApprovalRequest
    mark: StatusMark = StatusMark.WARN


class ApprovalDecided(_Event):
    type: Literal[EventType.APPROVAL_DECIDED] = EventType.APPROVAL_DECIDED
    decision: ApprovalDecision

    @model_validator(mode="after")
    def _mark_matches_decision(self) -> Self:
        expected = StatusMark.OK if self.decision.approved else StatusMark.WARN
        if self.mark is not expected:
            raise ValueError("a rejected approval must be marked WARN, an approved one OK")
        return self


class CycleStepCompleted(_Event):
    """One of the ten Monday steps. The weekly cycle streams too, not just the crisis."""

    type: Literal[EventType.CYCLE_STEP] = EventType.CYCLE_STEP
    step: Annotated[int, Field(ge=1, le=10)]
    name: ShortText
    forecast_version_id: str | None = None


WarRoomEvent: TypeAlias = Annotated[  # noqa: UP040 -- pydantic resolves this alias, PEP 695 it cannot
    StreamOpened
    | StreamHeartbeat
    | SystemDegraded
    | InvestigationOpened
    | InvestigationPhaseChanged
    | InvestigationClosed
    | PlanSelected
    | AgentQueued
    | AgentStarted
    | AgentToolCall
    | AgentFindingEmitted
    | AgentStatusChanged
    | EvidenceRejected
    | ConflictDetected
    | FollowupDispatched
    | ConflictResolved
    | ScenarioGenerated
    | StressCompleted
    | ReplanStarted
    | RecommendationReady
    | ApprovalRequested
    | ApprovalDecided
    | CycleStepCompleted,
    Field(discriminator="type"),
]

EVENT_ADAPTER: TypeAdapter[WarRoomEvent] = TypeAdapter(WarRoomEvent)


def parse_event(raw: str | bytes | dict[str, object]) -> WarRoomEvent:
    """Decode one event from the wire. Used by tests and by the fixture replayer."""
    if isinstance(raw, dict):
        return EVENT_ADAPTER.validate_python(raw)
    return EVENT_ADAPTER.validate_json(raw)


def event_json_schema() -> str:
    """The schema the frontend generates its TypeScript types from."""
    return json.dumps(EVENT_ADAPTER.json_schema(), indent=2, sort_keys=True)
