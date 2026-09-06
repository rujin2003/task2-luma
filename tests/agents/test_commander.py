"""The Commander: enumerated plans, justified omissions, and an honest fallback."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from backend.agents.provider import LLMRequest, LLMTimeout, RecordingMissing
from backend.agents.replay import ReplayProvider
from backend.agents.routing import ModelRouting
from backend.contracts import AgentRole, AgentStatus, EventType, SourceSystem, TokenUsage
from backend.contracts.constraints import ConstraintKind
from backend.orchestrator.bus import EventBus
from backend.orchestrator.commander import Commander
from backend.orchestrator.plans import (
    PLAN_CATALOGUE,
    PLANS_BY_ID,
    SPECIALIST_ROLES,
    available_plans,
    fallback_plan,
    plans_for,
)
from backend.orchestrator.runs import InMemoryRunStore
from backend.tools.results import CapabilityManifest
from tests.fixtures.llm._record import AS_OF, COMPANY, escalation_incident

FULL_MANIFEST = CapabilityManifest(
    available_sources=list(SourceSystem), missing_sources=[], references=[]
)
NO_DODO = CapabilityManifest(
    available_sources=[s for s in SourceSystem if s is not SourceSystem.DODO],
    missing_sources=[SourceSystem.DODO],
    references=[],
)


class FixedChoice:
    """Forces one plan on the Commander; every other role replays as usual.

    The plan shapes below have no golden recording of their own, and giving them one would
    mean freezing a fingerprint for a scenario the seeded fixtures do not produce. Pinning
    the choice and leaving the specialists on their recordings keeps the dispatch under
    test real.
    """

    name = "fixed"

    def __init__(self, plan_id: str, rationale: str = "test fixture") -> None:
        self.plan_id = plan_id
        self.rationale = rationale
        self._specialists = ReplayProvider()

    async def complete[OutputT: BaseModel](
        self, request: LLMRequest, schema: type[OutputT], *, timeout_s: float
    ) -> tuple[OutputT, TokenUsage]:
        if request.agent is not AgentRole.COMMANDER:
            return await self._specialists.complete(request, schema, timeout_s=timeout_s)
        return (
            schema.model_validate({"plan_id": self.plan_id, "rationale": self.rationale}),
            TokenUsage(input_tokens=100, output_tokens=20),
        )


class DeadProvider:
    name = "dead"

    async def complete[OutputT: BaseModel](
        self, request: LLMRequest, schema: type[OutputT], *, timeout_s: float
    ) -> tuple[OutputT, TokenUsage]:
        raise LLMTimeout("the free tier throttled the planning call")


async def commander(provider, bus: EventBus, toolset, routing: ModelRouting) -> Commander:
    return Commander(
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


# --- the catalogue itself -------------------------------------------------------------


def test_every_plan_justifies_every_agent_it_leaves_out() -> None:
    """Authoring a plan means authoring its refusals. The model is not asked to invent one."""
    for plan in PLAN_CATALOGUE:
        skips = {skipped.agent: skipped.reason for skipped in plan.skips(FULL_MANIFEST)}
        assert set(skips) == set(SPECIALIST_ROLES) - set(plan.invokes)
        assert all(len(reason) > 30 for reason in skips.values()), plan.plan_id


def test_a_pure_receivables_incident_does_not_invoke_dodo() -> None:
    """The test `PHASES.md` asks for by name, on the merits rather than on capability."""
    plan = PLANS_BY_ID["receivables-shortfall"]

    assert AgentRole.DODO_REVENUE not in plan.invokes
    reason = next(s.reason for s in plan.skips(FULL_MANIFEST) if s.agent is AgentRole.DODO_REVENUE)
    assert "subscription receipts are on plan" in reason
    assert "trade-AR" in reason


def test_a_missing_source_outranks_the_plans_own_judgement() -> None:
    """'No Dodo connection' and 'Dodo has nothing to add' are different facts."""
    plan = PLANS_BY_ID["receivables-shortfall"]

    reason = next(s.reason for s in plan.skips(NO_DODO) if s.agent is AgentRole.DODO_REVENUE)
    assert "no dodo source connected" in reason


def test_a_tenant_without_dodo_is_never_offered_a_dodo_plan() -> None:
    offered = {plan.plan_id for plan in available_plans(NO_DODO)}

    assert "subscription-decline" not in offered
    assert "full-liquidity-sweep" not in offered, "the sweep invokes Dodo"
    assert "receivables-shortfall" in offered


def test_the_menu_narrows_to_the_breach_kind() -> None:
    covenant_plans = {
        plan.plan_id for plan in plans_for(FULL_MANIFEST, [ConstraintKind.COVENANT_RATIO])
    }

    assert covenant_plans == {"covenant-headroom"}


def test_narrowing_to_nothing_falls_back_to_the_whole_runnable_menu() -> None:
    """A breach kind no plan claims must not leave the Commander with no options."""
    plans = plans_for(FULL_MANIFEST, [ConstraintKind.CLOSED_PERIOD])

    assert plans == available_plans(FULL_MANIFEST)


def test_the_fallback_is_the_widest_runnable_plan() -> None:
    assert fallback_plan(FULL_MANIFEST).plan_id == "full-liquidity-sweep"
    assert AgentRole.DODO_REVENUE not in fallback_plan(NO_DODO).invokes


# --- planning -------------------------------------------------------------------------


async def test_the_golden_path_selects_the_full_sweep(bus, toolset, routing) -> None:
    cmd = await commander(ReplayProvider(strict=True), bus, toolset, routing)

    dispatch = await cmd.plan((ConstraintKind.MIN_CASH,))

    assert dispatch.plan.plan_id == "full-liquidity-sweep"
    assert dispatch.model_selected
    assert set(dispatch.plan.invokes) == set(SPECIALIST_ROLES)
    assert dispatch.skipped == []


async def test_the_plan_and_its_omissions_are_streamed(bus, toolset, routing) -> None:
    cmd = await commander(FixedChoice("receivables-shortfall"), bus, toolset, routing)

    await cmd.plan((ConstraintKind.MIN_CASH,))

    selected = [e for e in bus.history() if e.type is EventType.PLAN_SELECTED]
    assert len(selected) == 1
    assert selected[0].plan_id == "receivables-shortfall"
    assert AgentRole.DODO_REVENUE not in selected[0].invoked
    assert {s.agent for s in selected[0].skipped} == {
        AgentRole.AP_OPTIMIZATION,
        AgentRole.SUPPLIER_RISK,
        AgentRole.DODO_REVENUE,
    }


async def test_a_plan_that_is_not_on_the_menu_is_refused(bus, toolset, routing) -> None:
    """A hallucinated plan id and an unrunnable one are the same failure from here."""
    cmd = await commander(FixedChoice("interrogate-the-vendors"), bus, toolset, routing)

    dispatch = await cmd.plan()

    assert dispatch.plan.plan_id == "full-liquidity-sweep"
    assert not dispatch.model_selected
    assert "was not on the menu" in dispatch.fallback_reason
    assert [e for e in bus.history() if e.type is EventType.SYSTEM_DEGRADED]


async def test_a_throttled_planning_call_still_produces_an_investigation(
    bus, toolset, routing
) -> None:
    cmd = await commander(DeadProvider(), bus, toolset, routing)

    dispatch = await cmd.plan()

    assert dispatch.plan.plan_id == "full-liquidity-sweep"
    assert not dispatch.model_selected
    assert "LLMTimeout" in dispatch.fallback_reason
    assert dispatch.rationale.startswith("Default plan:")


async def test_the_commander_stays_inside_its_allowlist(bus, toolset, routing) -> None:
    """It plans from shape and position. The variance bridge is not its to read."""
    cmd = await commander(ReplayProvider(strict=True), bus, toolset, routing)

    await cmd.plan((ConstraintKind.MIN_CASH,))

    called = {e.tool for e in bus.history() if e.type is EventType.AGENT_TOOL_CALL}
    assert called == {"get_capability_manifest", "get_liquidity_position", "get_covenant_status"}


async def test_an_unrecorded_incident_fails_loudly_under_strict_replay(
    bus, toolset, routing
) -> None:
    """The golden path must not quietly fall back to a plausible default."""
    cmd = Commander(
        provider=ReplayProvider(strict=True),
        toolset=toolset,
        routing=routing,
        bus=bus,
        company=COMPANY,
        as_of="2026-04-06",
        investigation_id="inv-2",
        incident=await escalation_incident(),
    )

    dispatch = await cmd.plan()

    assert not dispatch.model_selected
    assert RecordingMissing.__name__ in dispatch.fallback_reason


# --- dispatch -------------------------------------------------------------------------


async def test_the_wave_runs_every_invoked_agent(bus, toolset, routing) -> None:
    cmd = await commander(ReplayProvider(), bus, toolset, routing)

    dispatch = await cmd.investigate(await cmd.plan((ConstraintKind.MIN_CASH,)))

    assert {run.agent for run in dispatch.runs} == set(SPECIALIST_ROLES)
    assert len(dispatch.runs) == 6


async def test_supplier_risk_is_handed_the_ap_agents_actual_proposals(
    bus, toolset, routing
) -> None:
    """Rejecting a specific proposal is not something you can do from a digest line."""
    cmd = await commander(ReplayProvider(), bus, toolset, routing)

    dispatch = await cmd.investigate(await cmd.plan((ConstraintKind.MIN_CASH,)))

    supplier = next(run for run in dispatch.runs if run.agent is AgentRole.SUPPLIER_RISK)
    assert supplier.status is AgentStatus.COMPLETE
    assert supplier.finding is not None
    assert "BILL-8841" in supplier.finding.rejects
    assert supplier.context_pack is not None
    assert "BILL-8841" in supplier.context_pack, "it must see the row it is ruling on"


async def test_supplier_risk_runs_after_the_agent_it_challenges(bus, toolset, routing) -> None:
    cmd = await commander(ReplayProvider(), bus, toolset, routing)

    dispatch = await cmd.investigate(await cmd.plan((ConstraintKind.MIN_CASH,)))

    order = [run.agent for run in dispatch.runs]
    assert order.index(AgentRole.SUPPLIER_RISK) > order.index(AgentRole.AP_OPTIMIZATION)


async def test_a_narrow_plan_dispatches_only_its_own_agents(bus, toolset, routing) -> None:
    cmd = await commander(FixedChoice("receivables-shortfall"), bus, toolset, routing)

    dispatch = await cmd.investigate(await cmd.plan())

    assert {run.agent for run in dispatch.runs} == {
        AgentRole.FORECAST,
        AgentRole.VARIANCE,
        AgentRole.AR_COLLECTIONS,
    }
    assert not dispatch.degraded


@pytest.mark.parametrize("plan_id", sorted(PLANS_BY_ID))
async def test_every_plan_in_the_catalogue_dispatches(plan_id, bus, toolset, routing) -> None:
    """A plan nobody can run is a plan that should not be on the menu."""
    cmd = await commander(FixedChoice(plan_id), bus, toolset, routing)

    dispatch = await cmd.investigate(await cmd.plan())

    assert {run.agent for run in dispatch.runs} == set(dispatch.plan.invokes)
