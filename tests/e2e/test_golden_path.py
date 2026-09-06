"""End-to-end golden path: Monday cycle → breach → war room → stress fail → replan → recommendation."""

from __future__ import annotations

from backend.agents.fake import FakeProvider
from backend.agents.routing import load_routing
from backend.contracts.agent import AgentRole
from backend.contracts.events import EventType
from backend.contracts.provenance import SourceSystem
from backend.orchestrator.bus import EventBus
from backend.orchestrator.plans import select_plan
from backend.orchestrator.policy_check import check_liquidity_policy
from backend.orchestrator.weekly_cycle import run_monday_cycle
from backend.tools.fixtures import FixtureToolset
from backend.tools.results import CapabilityManifest


async def test_golden_path_produces_replan_and_recommendation() -> None:
    bus = EventBus()
    result = await run_monday_cycle(
        tools=FixtureToolset(),
        bus=bus,
        provider=FakeProvider(strict=True),
        routing=load_routing(),
        company="NovaTech Industries",
        as_of="2026-03-02",
    )

    assert result.published
    assert result.breach is not None
    assert result.investigation is not None

    inv = result.investigation
    assert inv.recommendation is not None
    assert len(inv.replan_history) >= 1
    assert inv.recommendation.selected_strategy.strategy_id.startswith("strategy-replan")
    assert any(r.action.document_ref == "BILL-8841" for r in inv.recommendation.rejected_actions)
    assert inv.recommendation.worklist
    assert inv.approvals

    types = {event.type for event in bus.history()}
    assert EventType.INVESTIGATION_OPENED in types
    assert EventType.PLAN_SELECTED in types
    assert EventType.CONFLICT_DETECTED in types
    assert EventType.CONFLICT_RESOLVED in types
    assert EventType.STRESS_COMPLETED in types
    assert EventType.REPLAN_STARTED in types
    assert EventType.RECOMMENDATION_READY in types
    assert EventType.APPROVAL_REQUESTED in types

    agents = {f.agent for f in inv.findings}
    assert AgentRole.SUPPLIER_RISK in agents
    assert AgentRole.VARIANCE in agents


async def test_pure_ar_plan_skips_dodo() -> None:
    from backend.contracts.constraints import (
        ConstraintKind,
        ConstraintSeverity,
        ConstraintViolation,
    )
    from backend.contracts.money import Money

    breach = ConstraintViolation(
        constraint_id="min_cash",
        kind=ConstraintKind.MIN_CASH,
        severity=ConstraintSeverity.HARD,
        description="AR shortfall",
        observed_money=Money(minor_units=1_800_000_000, currency="USD"),
        threshold_display="20000000.00 USD",
        week_index=6,
    )
    manifest = CapabilityManifest(
        available_sources=list(SourceSystem),
        missing_sources=[],
    )
    plan = select_plan(breach=breach, manifest=manifest, incident_kind="ar_shortfall")
    assert AgentRole.DODO_REVENUE not in plan.invoked
    assert AgentRole.AR_COLLECTIONS in plan.invoked


async def test_policy_check_trips_on_fixture_liquidity() -> None:
    tools = FixtureToolset()
    position = await tools.get_liquidity_position()
    covenants = await tools.get_covenant_status()
    policy = await tools.get_policy_constraints()
    breach = check_liquidity_policy(position, covenants, policy)
    assert breach is not None
    assert position.breaches_floor
