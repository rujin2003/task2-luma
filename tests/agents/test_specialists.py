"""The six specialists, replayed strictly against their golden recordings.

`strict=True` matters: the recordings are keyed on the fingerprint of the whole assembled
call, so if a prompt, a gather line or the Context Pack changes, the replay stops matching
and these tests fail. That is the regression suite -- a prompt edit is a deliberate act
with a re-recording, not something that drifts in unnoticed.

Every agent gets the same two questions asked of it: does it answer the question it exists
to answer, and does it refuse when it should.
"""

from __future__ import annotations

import pytest

from backend.agents.replay import ReplayProvider
from backend.agents.routing import ModelRouting
from backend.agents.runner import AgentRunner
from backend.agents.specialists import SPECIALISTS, ApOptimizationAgent, build
from backend.agents.specialists.supplier_risk import SupplierRiskAgent
from backend.contracts import AgentRole, AgentStatus
from backend.contracts.agent import ActionKind
from backend.orchestrator.bus import EventBus
from backend.tools.fixtures import FixtureToolset
from backend.tools.registry import ScopedToolset, tools_for
from tests.fixtures.llm._record import AP_PROPOSALS, PAYROLL_PRESSURE_TASK


@pytest.fixture
def runner(bus: EventBus, toolset: FixtureToolset, routing: ModelRouting) -> AgentRunner:
    return AgentRunner(
        provider=ReplayProvider(strict=True),
        toolset=toolset,
        routing=routing,
        bus=bus,
        company="NovaTech Industries",
        as_of="2026-03-02",
    )


def specialist(role: AgentRole):
    """The Supplier Risk agent is the one that takes another agent's output as input."""
    if role is AgentRole.SUPPLIER_RISK:
        return SupplierRiskAgent(proposals=AP_PROPOSALS)
    return build(role)


async def scoped_lines(spec, toolset: FixtureToolset) -> tuple[list[str], ScopedToolset]:
    scoped = ScopedToolset(toolset, spec.role)
    return await spec.gather(scoped), scoped


# --- every specialist -----------------------------------------------------------------


@pytest.mark.parametrize("role", sorted(SPECIALISTS, key=lambda r: r.value))
async def test_each_specialist_produces_a_cited_finding(role: AgentRole, runner) -> None:
    spec = specialist(role)

    run = await runner.run(spec, run_id=f"run-{role.value}")

    assert run.status is AgentStatus.COMPLETE, run.failure_reason
    assert run.finding is not None
    assert run.finding.evidence, "a complete finding cites at least one resolved row"
    assert run.usage.total > 0


@pytest.mark.parametrize("role", sorted(SPECIALISTS, key=lambda r: r.value))
async def test_each_specialist_stays_inside_its_allowlist(role: AgentRole, toolset) -> None:
    spec = specialist(role)

    _, scoped = await scoped_lines(spec, toolset)

    called = {call.tool for call in scoped.calls}
    assert called <= tools_for(role), f"{role.value} called outside its allowlist: {called}"
    assert called, "a specialist that calls no tool has nothing to cite"


@pytest.mark.parametrize("role", sorted(SPECIALISTS, key=lambda r: r.value))
async def test_a_brief_line_only_licenses_references_the_tools_returned(
    role: AgentRole, toolset
) -> None:
    """A line that cites something the run never saw is a fabrication waiting to happen."""
    spec = specialist(role)

    lines, scoped = await scoped_lines(spec, toolset)

    cited = {
        line.rsplit("[", 1)[1].rstrip("]") for line in lines if line.endswith("]") and "[" in line
    }
    assert cited <= scoped.references


# --- forecast -------------------------------------------------------------------------


async def test_the_forecast_agent_flags_the_stale_driver_and_proposes_nothing(runner) -> None:
    run = await runner.run(build(AgentRole.FORECAST), run_id="run-forecast")

    assert run.finding is not None
    assert "stale" in run.finding.headline.lower()
    assert run.finding.recommended_actions == [], "explaining is not proposing"


async def test_the_forecast_brief_names_the_breach_weeks_the_engine_flagged(toolset) -> None:
    lines, _ = await scoped_lines(build(AgentRole.FORECAST), toolset)

    breaches = [line for line in lines if "breaches the floor" in line]
    assert len(breaches) == 3, "W5, W6 and W7 breach in the seeded fixture"
    assert any("STALE" in line for line in lines)


# --- variance: the star ---------------------------------------------------------------


async def test_the_variance_agent_root_causes_the_seeded_miss(runner) -> None:
    """The one the Treasurer asks first: not what moved, but why, and does it repeat."""
    run = await runner.run(build(AgentRole.VARIANCE), run_id="run-variance")

    assert run.status is AgentStatus.COMPLETE
    finding = run.finding
    assert finding is not None
    assert "10482" in finding.detail, "the miss is traced to the invoice that caused it"
    references = {evidence.reference for evidence in finding.evidence}
    assert "ar_ledger:INV-10482#amount_due" in references
    assert finding.quantum is not None and finding.quantum.minor_units < 0
    assert finding.risks, "a cause that repeats is the part that changes Tuesday"


async def test_the_variance_brief_hands_over_the_total_rather_than_inviting_arithmetic(
    toolset,
) -> None:
    lines, _ = await scoped_lines(build(AgentRole.VARIANCE), toolset)

    assert any("total delta" in line for line in lines)
    assert sum("MATERIAL" in line for line in lines) == 3
    assert any("stale driver" in line for line in lines)


# --- AR collections -------------------------------------------------------------------


async def test_the_ar_agent_emits_worklist_rows_not_an_aggregate(runner) -> None:
    run = await runner.run(build(AgentRole.AR_COLLECTIONS), run_id="run-ar")

    assert run.finding is not None
    actions = run.finding.recommended_actions
    assert len(actions) >= 2
    assert all(action.counterparty and action.document_ref for action in actions), (
        "a row nobody can pick up on Monday is not a worklist row"
    )
    assert any(action.kind is ActionKind.DISPUTE_RESOLUTION for action in actions), (
        "a disputed invoice is a dispute, not a collection call"
    )


async def test_the_ar_brief_separates_open_ar_from_collectible_ar(toolset) -> None:
    lines, _ = await scoped_lines(build(AgentRole.AR_COLLECTIONS), toolset)

    assert any("not collectible" in line for line in lines)
    assert any("% likely ->" in line for line in lines)


# --- AP optimization and the refusal --------------------------------------------------


async def test_the_ap_agent_prices_the_discount_it_gives_up(runner) -> None:
    run = await runner.run(build(AgentRole.AP_OPTIMIZATION), run_id="run-ap")

    assert run.status is AgentStatus.COMPLETE
    assert run.finding is not None
    assert "discount" in run.finding.detail.lower()
    assert all(
        action.delay_days is not None
        for action in run.finding.recommended_actions
        if action.kind is ActionKind.AP_DEFER
    )


async def test_the_ap_agent_pressed_for_a_target_still_cannot_defer_payroll(runner) -> None:
    """The must-refuse case. The gate is code, so the answer does not depend on the model."""
    spec = build(AgentRole.AP_OPTIMIZATION, task=PAYROLL_PRESSURE_TASK)

    run = await runner.run(spec, run_id="run-ap-payroll")

    assert run.status is AgentStatus.REFUSED
    finding = run.finding
    assert finding is not None
    assert finding.recommended_actions == [], "the plan goes, not just the offending row"
    assert "protected-payroll" in finding.headline
    assert "PAY-2026-W12" in finding.detail
    assert [evidence.reference for evidence in finding.evidence] == [
        "policy:treasury-policy-v4#protected_classes"
    ]


async def test_the_ap_brief_shows_protected_rows_rather_than_hiding_them(toolset) -> None:
    """An agent that cannot see payroll cannot reason about why it is off limits."""
    lines, _ = await scoped_lines(build(AgentRole.AP_OPTIMIZATION), toolset)

    protected = [line for line in lines if "PROTECTED, not a lever" in line]
    assert len(protected) == 2, "payroll and statutory tax"
    assert any("policy protected-payroll" in line for line in lines)


def test_the_gate_leaves_a_compliant_plan_alone(finding) -> None:
    agent = ApOptimizationAgent()

    assert agent.review(finding) is finding


# --- supplier risk --------------------------------------------------------------------


async def test_the_supplier_risk_agent_rejects_a_specific_proposal_with_evidence(
    runner,
) -> None:
    run = await runner.run(SupplierRiskAgent(proposals=AP_PROPOSALS), run_id="run-supplier")

    assert run.status is AgentStatus.COMPLETE
    finding = run.finding
    assert finding is not None
    assert finding.rejects == ["BILL-8841"], "it names the proposal it rejects"
    assert "ap_ledger:supplier-acme#risk" in {e.reference for e in finding.evidence}
    assert "Globex" in finding.detail, "it says what it does not object to"


async def test_the_supplier_risk_brief_carries_the_proposals_under_review(toolset) -> None:
    lines, _ = await scoped_lines(SupplierRiskAgent(proposals=AP_PROPOSALS), toolset)

    assert sum(line.startswith("proposal ") for line in lines) == 2
    assert any("sole source" in line for line in lines)
    assert any("alternates available" in line for line in lines)


async def test_with_nothing_proposed_there_is_nothing_to_challenge(toolset) -> None:
    lines, _ = await scoped_lines(SupplierRiskAgent(), toolset)

    assert any("nothing to challenge" in line for line in lines)


# --- dodo -----------------------------------------------------------------------------


async def test_the_dodo_agent_separates_recoverable_from_at_risk(runner) -> None:
    run = await runner.run(build(AgentRole.DODO_REVENUE), run_id="run-dodo")

    assert run.finding is not None
    assert run.finding.quantum is not None
    assert run.finding.quantum.minor_units == 59760000, "the recoverable figure, not the total"
    retried = {
        reference
        for action in run.finding.recommended_actions
        for reference in action.evidence_refs
    }
    assert "dodo:decline-2026-W10#stolen_card" not in retried, "hard declines are not retried"


async def test_the_dodo_brief_states_the_window_or_says_there_is_none(toolset) -> None:
    lines, _ = await scoped_lines(build(AgentRole.DODO_REVENUE), toolset)

    hard = [line for line in lines if "(hard)" in line]
    assert hard and all("fee with no upside" in line for line in hard)
    assert any("retry window 14d" in line for line in lines)
