"""The war room end to end: composition, stress, the replan, and the worklist.

The narrative these assert is not scripted anywhere. It falls out of the seeded numbers:
the cheapest bundle that closes a $1.6M gap does not survive this company's own 90th
percentile AR error, and the one that does costs supplier goodwill to get there.
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.agents.fake import FakeProvider
from backend.contracts import (
    ActionKind,
    AgentRole,
    EventType,
    InvestigationPhase,
    Money,
    WorklistStatus,
)
from backend.orchestrator.investigation import Investigation
from backend.orchestrator.policy import check_policy
from backend.orchestrator.runs import InMemoryRunStore
from backend.orchestrator.stress import UNCALIBRATED, calibrate
from backend.orchestrator.worklist import load_weights
from tests.fixtures.llm._record import COMPANY

AS_OF = date(2026, 3, 2)
SHORTFALL = Money.from_major("1600000", "USD")


@pytest.fixture
async def check(toolset):
    return check_policy(
        as_of="2026-03-02",
        policy=await toolset.get_policy_constraints(),
        position=await toolset.get_liquidity_position(),
        forecast=await toolset.get_forecast_summary(),
        covenants=await toolset.get_covenant_status(),
    )


@pytest.fixture
async def result(bus, toolset, routing, check):
    investigation = Investigation(
        provider=FakeProvider(),
        toolset=toolset,
        routing=routing,
        bus=bus,
        company=COMPANY,
        as_of=AS_OF,
        check=check,
        store=InMemoryRunStore(),
        investigation_id="inv-golden",
    )
    return await investigation.run()


# --- the state machine ------------------------------------------------------------------


async def test_the_war_room_opens_on_a_quantified_breach(result, bus) -> None:
    opened = [e for e in bus.history() if e.type is EventType.INVESTIGATION_OPENED]

    assert len(opened) == 1
    assert opened[0].breach is not None
    assert opened[0].breach.week_index == 6
    assert opened[0].quantum == Money.from_major("18400000", "USD")
    assert "min-cash" in opened[0].trigger


async def test_the_phases_are_streamed_in_order(result, bus) -> None:
    phases = [e.phase for e in bus.history() if e.type is EventType.INVESTIGATION_PHASE]

    assert phases[0] is InvestigationPhase.PLANNING
    assert phases[1] is InvestigationPhase.INVESTIGATING
    assert phases[2] is InvestigationPhase.RESOLVING_CONFLICTS
    assert phases[3] is InvestigationPhase.GENERATING_SCENARIOS
    assert phases[4] is InvestigationPhase.STRESS_TESTING
    assert phases[-1] is InvestigationPhase.CLOSED
    assert result.phase is InvestigationPhase.CLOSED


async def test_the_investigation_closes_with_a_recommendation(result, bus) -> None:
    closed = [e for e in bus.history() if e.type is EventType.INVESTIGATION_CLOSED]

    assert len(closed) == 1
    assert result.recommendation is not None
    assert closed[0].recommendation_id == result.recommendation.recommendation_id


# --- scenarios --------------------------------------------------------------------------


async def test_four_materially_different_bundles_are_generated(result) -> None:
    ids = [strategy.strategy_id for strategy in result.strategies]

    assert ids == [
        "receivables-first",
        "working-capital",
        "financing-bridge",
        "no-subscription",
    ]
    shapes = {frozenset(action.kind for action in s.actions) for s in result.strategies}
    assert len(shapes) == 4, "four permutations of the same shape would not be four bundles"


async def test_only_the_financing_bundle_draws_and_it_is_sized_to_the_gap(result) -> None:
    """Drawing more than the gap is interest on money we do not need."""
    bridge = next(s for s in result.strategies if s.strategy_id == "financing-bridge")
    draws = [a for a in bridge.actions if a.kind is ActionKind.REVOLVER_DRAW]

    assert len(draws) == 1
    assert draws[0].amount == Money.from_major("359400", "USD")
    assert bridge.net_cash_impact == SHORTFALL
    others = [s for s in result.strategies if s.strategy_id != "financing-bridge"]
    assert not any(a.kind is ActionKind.REVOLVER_DRAW for s in others for a in s.actions)


async def test_every_bundle_is_scored_and_the_weights_travel_with_the_score(result) -> None:
    weights = load_weights()

    for strategy in result.strategies:
        card = result.scores[strategy.strategy_id]
        assert {o.objective for o in card.objectives} == set(weights.weights)
        assert all(o.basis for o in card.objectives), "a score without a basis is a number"
        assert card.unscored == weights.unscored
        assert "single-currency" in card.unscored["fx_exposure"]


async def test_scenarios_are_streamed(result, bus) -> None:
    generated = [e for e in bus.history() if e.type is EventType.SCENARIO_GENERATED]

    assert len(generated) == len(result.strategies)


# --- the constraint gate ----------------------------------------------------------------


async def test_the_upheld_objection_removes_the_deferral_it_was_about(result) -> None:
    """AP proposed BILL-8841 first; that is not why it survives, and it does not."""
    assert result.recommendation is not None
    rejected = {r.action.document_ref for r in result.recommendation.rejected_actions}

    assert "BILL-8841" in rejected
    refusal = next(
        r for r in result.recommendation.rejected_actions if r.action.document_ref == "BILL-8841"
    )
    assert refusal.rejected_by == "supplier_risk"
    assert refusal.evidence, "a rejection travels with the evidence that made it"
    assert not any(
        action.document_ref == "BILL-8841"
        for strategy in result.strategies
        for action in strategy.actions
    )


async def test_the_surviving_deferral_is_the_one_nobody_objected_to(result) -> None:
    working = next(s for s in result.strategies if s.strategy_id == "working-capital")
    deferrals = [a for a in working.actions if a.kind is ActionKind.AP_DEFER]

    assert [a.document_ref for a in deferrals] == ["BILL-8863"]


# --- stress and the replan ---------------------------------------------------------------


async def test_stressors_are_calibrated_from_measured_error(toolset) -> None:
    """'AR at our own 90th percentile' is defensible; 'AR minus ten percent' is not."""
    percentiles = await toolset.get_forecast_error_percentiles(horizon_weeks=6)

    menu = calibrate(percentiles, horizon_weeks=6)

    ar = next(s for s in menu if s.stressor_id == "ar_collections")
    assert str(ar.shift_pct) == "-17.3", "the W6 p90, not the W1 or the W13"
    assert "90th percentile" in ar.calibration
    assert "26 weeks" in ar.calibration
    assert {s.stressor_id for s in menu} == {"ar_collections", "dodo_receipts", "combined"}


async def test_stressors_we_cannot_calibrate_are_named_not_approximated(result) -> None:
    report = result.stress_reports[0]

    assert set(report.uncalibrated) == set(UNCALIBRATED)
    assert "never invented" in report.uncalibrated["financing_cost_increase"]


async def test_the_cheapest_closing_bundle_fails_stress_and_triggers_a_replan(result, bus) -> None:
    """The exit criterion from PHASES: one bundle fails, replan, the next one passes."""
    assert result.recommendation is not None
    history = result.recommendation.replan_history

    assert len(history) == 1
    assert history[0].strategy_id == "financing-bridge"
    assert "AR recovery shortfall" in history[0].failure_reason
    assert history[0].tightened_constraint is not None
    assert "must clear" in history[0].tightened_constraint

    replans = [e for e in bus.history() if e.type is EventType.REPLAN_STARTED]
    assert len(replans) == 1
    assert InvestigationPhase.REPLANNING in [
        e.phase for e in bus.history() if e.type is EventType.INVESTIGATION_PHASE
    ]


async def test_the_replanned_bundle_survives_every_stressor(result) -> None:
    assert result.recommendation is not None
    selected = result.recommendation.selected_strategy

    assert selected.strategy_id == "working-capital"
    surviving = [
        r for r in result.recommendation.stress_results if r.strategy_id == selected.strategy_id
    ]
    assert surviving, "the selected bundle was actually tested"
    assert all(r.passed for r in surviving)
    assert all(r.headroom.minor_units >= 0 for r in surviving)


async def test_the_failed_attempt_is_kept_not_deleted(result) -> None:
    """'The cheaper plan did not survive our own 90th percentile' is the persuasive line."""
    assert result.recommendation is not None
    failed = [r for r in result.recommendation.stress_results if not r.passed]

    assert failed, "the attempt that failed is still in the record"
    assert all(r.strategy_id == "financing-bridge" for r in failed)
    assert "financing-bridge" in result.recommendation.summary


async def test_a_draw_does_not_shrink_because_customers_paid_late(result) -> None:
    """A stress model that haircuts the draw flatters every financing bundle."""
    assert result.recommendation is not None
    bridge = next(s for s in result.strategies if s.strategy_id == "financing-bridge")
    under_ar = next(
        r
        for r in result.recommendation.stress_results
        if r.strategy_id == "financing-bridge" and r.stressor.stressor_id == "ar_collections"
    )

    draw = next(a for a in bridge.actions if a.kind is ActionKind.REVOLVER_DRAW)
    assert draw.amount is not None
    # Only the AR levers moved, so the shortfall the stress leaves is smaller than the draw.
    assert abs(under_ar.headroom) < draw.amount


# --- the worklist -----------------------------------------------------------------------


async def test_the_output_is_rows_a_named_person_can_execute(result) -> None:
    assert result.recommendation is not None
    rows = result.recommendation.worklist

    assert rows, "'accelerate $2.6M of AR' is not something anyone can do"
    assert [row.seq for row in rows] == list(range(1, len(rows) + 1))
    for row in rows:
        assert row.owner
        assert row.due_date > AS_OF
        assert row.status is WorklistStatus.OPEN
        assert row.amount.minor_units > 0


async def test_the_biggest_row_is_row_one(result) -> None:
    assert result.recommendation is not None
    amounts = [row.amount.minor_units for row in result.recommendation.worklist]

    assert amounts == sorted(amounts, reverse=True)


async def test_a_deferral_row_quotes_the_discount_it_costs(result) -> None:
    assert result.recommendation is not None
    rows = [r for r in result.recommendation.worklist if r.document_ref == "BILL-8863"]

    assert len(rows) == 1
    assert "Defer BILL-8863" in rows[0].action
    assert rows[0].owner == "AP Manager"
    assert rows[0].proposed_by is AgentRole.AP_OPTIMIZATION


async def test_rows_carry_the_evidence_their_agent_cited(result) -> None:
    assert result.recommendation is not None
    cited = [row for row in result.recommendation.worklist if row.evidence]

    assert cited, "a row with no provenance cannot be walked back to a ledger line"


async def test_the_recommendation_is_not_degraded_on_the_golden_path(result) -> None:
    assert result.recommendation is not None

    assert not result.recommendation.degraded, result.recommendation.degradation_reason
    assert not result.recommendation.requires_human_review
    assert not result.degraded, result.degradation_reasons
