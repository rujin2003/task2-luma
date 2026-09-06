"""Conflicts: detected by code, resolved by evidence, defaulted by a stated rule."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from backend.agents.fake import FakeProvider
from backend.agents.provider import LLMRequest, LLMTimeout
from backend.contracts import (
    ActionKind,
    AgentFinding,
    AgentRole,
    AgentStatus,
    ConflictKind,
    EventType,
    Evidence,
    Money,
    ProposedAction,
    SourceSystem,
    TokenUsage,
)
from backend.orchestrator.commander import Commander
from backend.orchestrator.conflicts import (
    MAX_CONFLICTS,
    ConflictResolver,
    detect,
)
from backend.orchestrator.runs import InMemoryRunStore
from tests.fixtures.llm._record import AS_OF, COMPANY, escalation_incident


def evidence(reference: str, excerpt: str = "a source row") -> Evidence:
    return Evidence(
        reference=reference, source=SourceSystem(reference.split(":", 1)[0]), excerpt=excerpt
    )


def proposal(document: str, amount: str | None = None, **extra) -> ProposedAction:
    return ProposedAction(
        kind=ActionKind.AP_DEFER,
        rationale="terms allow it",
        counterparty="Acme Components",
        document_ref=document,
        delay_days=21,
        amount=Money.from_major(amount, "USD") if amount else None,
        **extra,
    )


def finding(
    agent: AgentRole,
    *,
    actions: list[ProposedAction] | None = None,
    rejects: list[str] | None = None,
    refs: list[str] | None = None,
) -> AgentFinding:
    return AgentFinding(
        agent=agent,
        status=AgentStatus.COMPLETE,
        headline=f"{agent.value} position",
        evidence=[evidence(ref) for ref in (refs or ["ap_ledger:BILL-8841#due_date"])],
        recommended_actions=actions or [],
        rejects=rejects or [],
    )


# --- detection ------------------------------------------------------------------------


def test_a_rejection_of_a_proposed_row_is_a_semantic_conflict() -> None:
    conflicts = detect(
        [
            finding(AgentRole.AP_OPTIMIZATION, actions=[proposal("BILL-8841")]),
            finding(AgentRole.SUPPLIER_RISK, rejects=["BILL-8841"]),
        ]
    )

    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.kind is ConflictKind.SEMANTIC
    assert conflict.agents == (AgentRole.AP_OPTIMIZATION, AgentRole.SUPPLIER_RISK)
    assert conflict.subject == "BILL-8841"
    assert conflict.objector is AgentRole.SUPPLIER_RISK
    assert "BILL-8841" in conflict.question


def test_an_agent_cannot_conflict_with_itself() -> None:
    assert (
        detect(
            [
                finding(
                    AgentRole.AP_OPTIMIZATION,
                    actions=[proposal("BILL-8841")],
                    rejects=["BILL-8841"],
                )
            ]
        )
        == []
    )


def test_a_rejection_of_a_row_nobody_proposed_is_not_a_conflict() -> None:
    assert detect([finding(AgentRole.SUPPLIER_RISK, rejects=["BILL-9999"])]) == []


def test_two_materially_different_figures_on_one_document_are_structural() -> None:
    conflicts = detect(
        [
            finding(AgentRole.AP_OPTIMIZATION, actions=[proposal("BILL-8841", "1100000")]),
            finding(AgentRole.AR_COLLECTIONS, actions=[proposal("BILL-8841", "820000")]),
        ]
    )

    assert len(conflicts) == 1
    assert conflicts[0].kind is ConflictKind.STRUCTURAL
    assert conflicts[0].delta == Money.from_major("280000", "USD")
    assert "gap" in conflicts[0].description


def test_agreement_inside_tolerance_is_rounding_not_a_conflict() -> None:
    """Calling rounding a conflict trains a treasurer to ignore the conflict panel."""
    conflicts = detect(
        [
            finding(AgentRole.AP_OPTIMIZATION, actions=[proposal("BILL-8841", "1100000")]),
            finding(AgentRole.AR_COLLECTIONS, actions=[proposal("BILL-8841", "1080000")]),
        ]
    )

    assert conflicts == []


def test_a_wide_disagreement_about_a_trivial_amount_is_not_a_conflict() -> None:
    conflicts = detect(
        [
            finding(AgentRole.AP_OPTIMIZATION, actions=[proposal("BILL-8841", "900")]),
            finding(AgentRole.AR_COLLECTIONS, actions=[proposal("BILL-8841", "500")]),
        ]
    )

    assert conflicts == []


def test_the_follow_up_goes_to_the_side_with_less_on_the_record() -> None:
    conflicts = detect(
        [
            finding(
                AgentRole.AP_OPTIMIZATION,
                actions=[proposal("BILL-8841", "1100000")],
                refs=["ap_ledger:BILL-8841#due_date", "ap_ledger:BILL-8841#terms"],
            ),
            finding(
                AgentRole.AR_COLLECTIONS,
                actions=[proposal("BILL-8841", "820000")],
                refs=["ar_ledger:curve-2026-W09"],
            ),
        ]
    )

    assert conflicts[0].followup_to is AgentRole.AR_COLLECTIONS


def test_detection_is_bounded() -> None:
    """An investigation that chases contradictions until it runs out never terminates."""
    findings = [
        finding(AgentRole.AP_OPTIMIZATION, actions=[proposal(f"BILL-{n}") for n in range(10)]),
        finding(AgentRole.SUPPLIER_RISK, rejects=[f"BILL-{n}" for n in range(10)]),
    ]

    assert len(detect(findings)) == MAX_CONFLICTS


# --- resolution -----------------------------------------------------------------------


async def resolver(provider, bus, toolset, routing) -> ConflictResolver:
    return ConflictResolver(
        provider=provider,
        toolset=toolset,
        routing=routing,
        bus=bus,
        company=COMPANY,
        as_of=AS_OF,
        investigation_id="inv-1",
        incident=await escalation_incident(),
        store=InMemoryRunStore(),
    )


@pytest.fixture
async def wave(bus, toolset, routing):
    """The real golden-path wave, so the conflict under test is one the data produced.

    Strict replay: every agent in it is answering the brief it was actually recorded
    against, so the conflict below is the one the seeded data produces and not one a
    role default happened to fall into.
    """
    commander = Commander(
        provider=FakeProvider(strict=True),
        toolset=toolset,
        routing=routing,
        bus=bus,
        company=COMPANY,
        as_of=AS_OF,
        investigation_id="inv-1",
        incident=await escalation_incident(),
    )
    dispatch = await commander.investigate(await commander.plan())
    return dispatch.findings


async def test_the_seeded_data_produces_a_real_conflict(wave) -> None:
    """Not theatre: Acme is sole-source and carries a discount on the bill AP wants to defer."""
    conflicts = detect(wave)

    assert len(conflicts) == 1
    assert conflicts[0].subject == "BILL-8841"
    assert conflicts[0].kind is ConflictKind.SEMANTIC


async def test_resolution_dispatches_a_scoped_follow_up(wave, bus, toolset, routing) -> None:
    res = await resolver(FakeProvider(), bus, toolset, routing)

    resolved = await res.resolve(detect(wave)[0], wave)

    dispatched = [e for e in bus.history() if e.type is EventType.FOLLOWUP_DISPATCHED]
    assert len(dispatched) == 1
    assert dispatched[0].agent is AgentRole.SUPPLIER_RISK
    assert "BILL-8841" in dispatched[0].question
    assert resolved.followup is not None
    assert resolved.followup.status is AgentStatus.COMPLETE


async def test_resolution_upholds_a_side_on_rows_that_resolve(wave, bus, toolset, routing) -> None:
    res = await resolver(FakeProvider(), bus, toolset, routing)

    resolved = await res.resolve(detect(wave)[0], wave)

    assert resolved.model_resolved
    assert resolved.upheld is AgentRole.SUPPLIER_RISK
    assert resolved.overruled is AgentRole.AP_OPTIMIZATION
    assert resolved.evidence, "a resolution with no evidence is a coin flip"
    assert "sole source" in resolved.resolution.lower()


async def test_a_resolution_citing_rows_nobody_produced_is_discarded(
    wave, bus, toolset, routing
) -> None:
    """The one failure that would let a fabricated citation into a treasurer's answer."""

    class Fabricator:
        name = "fabricator"

        def __init__(self) -> None:
            self._specialists = FakeProvider()

        async def complete[OutputT: BaseModel](
            self, request: LLMRequest, schema: type[OutputT], *, timeout_s: float
        ) -> tuple[OutputT, TokenUsage]:
            if request.agent is not AgentRole.CONFLICT_RESOLUTION:
                return await self._specialists.complete(request, schema, timeout_s=timeout_s)
            return (
                schema.model_validate(
                    {
                        "upheld": "ap_optimization",
                        "resolution": "The alternate supplier memo clears the deferral.",
                        "evidence_refs": ["ap_ledger:memo-that-does-not-exist#alternates"],
                    }
                ),
                TokenUsage(input_tokens=10, output_tokens=10),
            )

    res = await resolver(Fabricator(), bus, toolset, routing)

    resolved = await res.resolve(detect(wave)[0], wave)

    assert not resolved.model_resolved
    assert "no reference that resolves" in resolved.fallback_reason
    assert resolved.upheld is AgentRole.SUPPLIER_RISK, "the objection stands by rule"
    assert "Settled by rule" in resolved.resolution


async def test_a_dead_resolver_still_settles_the_conflict(wave, bus, toolset, routing) -> None:
    class DeadResolver:
        name = "dead"

        def __init__(self) -> None:
            self._specialists = FakeProvider()

        async def complete[OutputT: BaseModel](
            self, request: LLMRequest, schema: type[OutputT], *, timeout_s: float
        ) -> tuple[OutputT, TokenUsage]:
            if request.agent is not AgentRole.CONFLICT_RESOLUTION:
                return await self._specialists.complete(request, schema, timeout_s=timeout_s)
            raise LLMTimeout("the resolver call was throttled")

    res = await resolver(DeadResolver(), bus, toolset, routing)

    resolved = await res.resolve(detect(wave)[0], wave)

    assert not resolved.model_resolved
    assert "LLMTimeout" in resolved.fallback_reason
    assert resolved.upheld is AgentRole.SUPPLIER_RISK
    assert resolved.evidence, "a defaulted resolution still shows the rows it declined to weigh"
    degraded = [e for e in bus.history() if e.type is EventType.SYSTEM_DEGRADED]
    assert any("settled by rule" in e.status_line for e in degraded)


async def test_the_resolution_is_streamed_with_its_evidence(wave, bus, toolset, routing) -> None:
    res = await resolver(FakeProvider(), bus, toolset, routing)

    await res.resolve(detect(wave)[0], wave)

    detected = [e for e in bus.history() if e.type is EventType.CONFLICT_DETECTED]
    resolved = [e for e in bus.history() if e.type is EventType.CONFLICT_RESOLVED]
    assert len(detected) == len(resolved) == 1
    assert detected[0].conflict_id == resolved[0].conflict_id
    assert resolved[0].evidence, "the event schema will not carry an unevidenced resolution"
    assert detected[0].seq < resolved[0].seq
