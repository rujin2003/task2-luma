"""The event schema is frozen. These tests are the freeze.

The frontend is built against this stream before any agent exists, so a rename here is a
break for a codebase nobody can grep. If one of these fails, the change is either wrong or
it is a deliberate major version bump -- and then the golden list below moves with it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.contracts import (
    EVENT_SCHEMA_VERSION,
    MARK_GLYPH,
    STATUS_TO_MARK,
    AgentFinding,
    AgentFindingEmitted,
    AgentRole,
    AgentStatus,
    AgentStatusChanged,
    ApprovalDecided,
    ApprovalDecision,
    ApprovalRole,
    ConflictDetected,
    ConflictKind,
    EventType,
    InvestigationClosed,
    InvestigationPhase,
    Money,
    PlanSelected,
    SkippedAgent,
    StatusMark,
    StreamOpened,
    StressCompleted,
    Stressor,
    StressResult,
    event_json_schema,
    parse_event,
)

TS = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)

# The frozen vocabulary, v1.0.0. Additive change only.
FROZEN_EVENT_TYPES = frozenset(
    {
        "stream.opened",
        "stream.heartbeat",
        "system.degraded",
        "investigation.opened",
        "investigation.phase",
        "investigation.closed",
        "plan.selected",
        "agent.queued",
        "agent.started",
        "agent.tool_call",
        "agent.finding",
        "agent.status",
        "evidence.rejected",
        "conflict.detected",
        "conflict.followup",
        "conflict.resolved",
        "scenario.generated",
        "stress.completed",
        "replan.started",
        "recommendation.ready",
        "approval.requested",
        "approval.decided",
        "cycle.step",
    }
)

# Every client renders from these four without knowing the payload.
ENVELOPE_FIELDS = frozenset({"schema_version", "event_id", "seq", "ts", "mark", "status_line"})

# Nothing in this stream may carry a model's reasoning.
FORBIDDEN_FIELD_SUBSTRINGS = ("thought", "reasoning", "chain_of", "scratchpad", "prompt_text")


def test_the_vocabulary_has_not_drifted() -> None:
    assert {t.value for t in EventType} == FROZEN_EVENT_TYPES
    assert EVENT_SCHEMA_VERSION == "1.0.0"


def test_every_event_type_has_a_model_and_the_envelope() -> None:
    schema = json.loads(event_json_schema())
    models = schema["$defs"]
    discriminated = schema["discriminator"]["mapping"]
    assert set(discriminated) == FROZEN_EVENT_TYPES

    for ref in discriminated.values():
        model = models[ref.rsplit("/", 1)[-1]]
        assert set(model["properties"]) >= ENVELOPE_FIELDS, model["title"]
        assert "status_line" in model["required"] and "seq" in model["required"]


def test_no_event_exposes_chain_of_thought() -> None:
    schema = json.loads(event_json_schema())
    for name in _property_names(schema):
        for banned in FORBIDDEN_FIELD_SUBSTRINGS:
            assert banned not in name, f"field {name!r} leaks reasoning into the event schema"


def _property_names(node: object) -> set[str]:
    """Every field name reachable in the schema, at any depth."""
    found: set[str] = set()
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            found |= {str(key).lower() for key in properties}
        for value in node.values():
            found |= _property_names(value)
    elif isinstance(node, list):
        for item in node:
            found |= _property_names(item)
    return found


def test_every_mark_has_a_glyph_and_every_agent_status_maps_to_one() -> None:
    assert set(MARK_GLYPH) == set(StatusMark)
    assert set(STATUS_TO_MARK) == set(AgentStatus)


def test_sse_frame_carries_the_resume_cursor_and_type() -> None:
    event = StreamOpened(seq=7, ts=TS, investigation_id="inv-1", status_line="stream opened")
    frame = event.to_sse()
    assert frame.startswith("id: 7\nevent: stream.opened\ndata: {")
    assert frame.endswith("\n\n")

    payload = json.loads(frame.split("data: ", 1)[1])
    assert parse_event(payload) == event


def test_the_discriminator_picks_the_right_model(finding: AgentFinding) -> None:
    event = AgentFindingEmitted(
        seq=3,
        ts=TS,
        agent=AgentRole.AR_COLLECTIONS,
        run_id="run-1",
        finding=finding,
        mark=StatusMark.OK,
        status_line=finding.headline,
    )
    round_tripped = parse_event(event.model_dump_json())
    assert isinstance(round_tripped, AgentFindingEmitted)
    assert round_tripped.finding.quantum == Money.from_major("2600000", "USD")


def test_display_matches_the_agent_ux_spec(finding: AgentFinding) -> None:
    event = AgentFindingEmitted(
        seq=3,
        ts=TS,
        agent=AgentRole.AR_COLLECTIONS,
        run_id="run-1",
        finding=finding,
        mark=StatusMark.OK,
        status_line="AR Agent: found $2.6M realistic acceleration opportunity",
    )
    assert event.display() == "✓ AR Agent: found $2.6M realistic acceleration opportunity"


def test_a_finding_event_cannot_be_attributed_to_another_agent(finding: AgentFinding) -> None:
    with pytest.raises(ValidationError):
        AgentFindingEmitted(
            seq=3,
            ts=TS,
            agent=AgentRole.SUPPLIER_RISK,
            run_id="run-1",
            finding=finding,
            status_line="misattributed",
        )


def test_a_timeout_event_must_say_why() -> None:
    with pytest.raises(ValidationError):
        AgentStatusChanged(
            seq=4,
            ts=TS,
            agent=AgentRole.DODO_REVENUE,
            run_id="run-2",
            status=AgentStatus.TIMEOUT,
            mark=StatusMark.FAIL,
            status_line="Dodo Agent timed out",
        )


def test_a_plan_justifies_the_agents_it_skipped() -> None:
    """A pure-AR incident must be able to say, on the wire, why Dodo was not called."""
    event = PlanSelected(
        seq=2,
        ts=TS,
        plan_id="ar-shortfall",
        invoked=[AgentRole.AR_COLLECTIONS, AgentRole.VARIANCE],
        skipped=[
            SkippedAgent(
                agent=AgentRole.DODO_REVENUE,
                reason="incident is confined to enterprise AR; no Dodo volume in scope",
            )
        ],
        status_line="Commander selected plan ar-shortfall",
    )
    assert AgentRole.DODO_REVENUE not in event.invoked
    assert event.skipped[0].reason


def test_a_conflict_names_at_least_two_agents() -> None:
    with pytest.raises(ValidationError):
        ConflictDetected(
            seq=5,
            ts=TS,
            conflict_id="c1",
            kind=ConflictKind.SEMANTIC,
            agents=[AgentRole.AP_OPTIMIZATION],
            description="one agent cannot disagree with itself",
            status_line="conflict detected",
        )


def test_a_failed_stress_result_is_marked_warn() -> None:
    result = StressResult(
        strategy_id="s4",
        stressor=Stressor(
            stressor_id="ar-p90",
            label="AR at the 90th percentile of our own 26-week error",
            category="ar",
            shift_pct=Decimal("-12.5"),
        ),
        min_cash=Money.from_major("18000000", "USD"),
        min_cash_week=6,
        floor=Money.from_major("20000000", "USD"),
        passed=False,
        headroom=Money.from_major("-2000000", "USD"),
    )
    assert StressCompleted(
        seq=6, ts=TS, result=result, mark=StatusMark.WARN, status_line="Strategy #4 fails downside"
    )
    with pytest.raises(ValidationError):
        StressCompleted(
            seq=6, ts=TS, result=result, mark=StatusMark.OK, status_line="Strategy #4 passes"
        )


def test_a_rejected_approval_is_marked_warn() -> None:
    decision = ApprovalDecision(
        request_id="req-1",
        decided_by="cfo@novatech",
        decided_by_role=ApprovalRole.CFO,
        approved=False,
        reason="prefer to collect before drawing",
        decided_at=TS,
    )
    with pytest.raises(ValidationError):
        ApprovalDecided(
            seq=9, ts=TS, decision=decision, mark=StatusMark.OK, status_line="CFO decided"
        )


def test_a_failed_investigation_states_why() -> None:
    with pytest.raises(ValidationError):
        InvestigationClosed(
            seq=10,
            ts=TS,
            phase=InvestigationPhase.FAILED,
            status_line="investigation failed",
        )
