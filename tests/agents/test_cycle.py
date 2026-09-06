"""The Monday cycle end to end, and the honest failures on the way through it."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from backend.agents.replay import ReplayProvider
from backend.agents.routing import ModelRouting
from backend.agents.runner import AgentRunner
from backend.contracts import AgentRole, ApprovalRole, EventType, Money, Override
from backend.contracts.approvals import SegregationOfDutiesError
from backend.orchestrator.bus import EventBus
from backend.orchestrator.cycle import CYCLE_STEPS, LAST_AUTOMATED_STEP, WeeklyCycle
from backend.orchestrator.review import (
    CloseCalendar,
    ClosedPeriodError,
    ClosePeriod,
    ReviewLedger,
)
from backend.orchestrator.runs import InMemoryRunStore
from backend.tools.fixtures import FixtureToolset

AS_OF = "2026-03-02"
COMPANY = "NovaTech Industries"
TS = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def cycle(bus: EventBus, toolset: FixtureToolset, routing: ModelRouting) -> WeeklyCycle:
    runner = AgentRunner(
        provider=ReplayProvider(),
        toolset=toolset,
        routing=routing,
        bus=bus,
        company=COMPANY,
        as_of=AS_OF,
    )
    return WeeklyCycle(
        toolset=toolset,
        bus=bus,
        company=COMPANY,
        as_of=AS_OF,
        runner=runner,
        store=InMemoryRunStore(),
        cycle_id="cycle-2026-W10",
    )


@pytest.fixture
def ledger() -> ReviewLedger:
    return ReviewLedger(prepared_by="analyst@novatech")


# --- steps 1-7 ------------------------------------------------------------------------


async def test_the_automated_half_runs_every_step_and_stops_at_the_review_gate(cycle) -> None:
    result = await cycle.run()

    assert result.steps_completed == list(CYCLE_STEPS[:LAST_AUTOMATED_STEP])
    assert CYCLE_STEPS[7] not in result.steps_completed, "review is the human's, not ours"
    assert result.forecast_version_id == "fv-2026-W10"
    assert not result.degraded, result.degradation_reasons


async def test_every_step_is_streamed_in_order(cycle, bus: EventBus) -> None:
    await cycle.run()

    steps = [event.step for event in bus.history() if event.type is EventType.CYCLE_STEP]
    assert steps == list(range(1, LAST_AUTOMATED_STEP + 1))


async def test_only_material_rows_are_explained(cycle) -> None:
    """Explaining a $30K variance signals you have never done this job."""
    result = await cycle.run()

    material = [row for row in result.bridge if row.material]
    immaterial = [row for row in result.bridge if not row.material]

    assert len(material) == 3, "AR, AP and Dodo clear materiality in the seeded bridge"
    assert immaterial, "the operating-expense row is below threshold"
    assert all(row.explained_by is None for row in immaterial)
    assert all(row.evidence == [] for row in immaterial)


async def test_the_bridge_ties_to_the_total_delta(cycle) -> None:
    result = await cycle.run()

    total = Money.zero("USD")
    for row in result.bridge:
        total = total + row.delta
    assert total == result.total_delta


async def test_the_variance_agent_root_causes_the_material_rows(cycle) -> None:
    result = await cycle.run()

    explained = [row for row in result.bridge if row.explained_by is not None]
    assert explained, "the seeded recording cites AR, AP and Dodo rows"
    assert all(row.explained_by is AgentRole.VARIANCE for row in explained)
    assert all(row.evidence for row in explained)
    # The excerpt on screen is the ledger row's, not the model's paraphrase of it.
    assert all("model would like" not in row.evidence[0].excerpt for row in explained)


async def test_the_immaterial_bucket_states_its_own_basis(cycle) -> None:
    result = await cycle.run()

    assert "immaterial row" in result.immaterial_basis
    assert "materiality threshold" in result.immaterial_basis


# --- step 7: exceptions ---------------------------------------------------------------


async def test_exceptions_surface_the_breach_weeks_and_the_stale_driver(cycle) -> None:
    result = await cycle.run()

    critical = result.critical_exceptions()
    assert {item.week_index for item in critical} == {5, 6, 7}

    stale = [item for item in result.exceptions if item.exception_id.startswith("stale-")]
    assert len(stale) == 1
    assert "soft-decline recovery rate" in stale[0].headline


async def test_exceptions_are_not_a_dump_of_everything(cycle) -> None:
    """Surfacing every row is the same as surfacing none of them."""
    result = await cycle.run()

    assert len(result.exceptions) < len(result.bridge) + len(result.drivers) + 13
    assert all(item.severity in {"critical", "warning", "info"} for item in result.exceptions)


async def test_an_unexplainable_variance_says_so_rather_than_going_blank(
    bus: EventBus, toolset: FixtureToolset, routing: ModelRouting
) -> None:
    """A material delta nobody accounted for is information, not an empty cell."""
    cycle = WeeklyCycle(toolset=toolset, bus=bus, company=COMPANY, as_of=AS_OF, runner=None)

    result = await cycle.run()

    assert result.degraded
    assert len(result.unexplained) == 3
    assert all("no agent runtime" in row.unexplained_reason for row in result.unexplained)
    assert [item for item in result.exceptions if item.exception_id.startswith("unexplained-")]


# --- steps 8 and 9: the human ones ----------------------------------------------------


def override(target: str = "dodo:decline-2026-W10#soft_rate") -> Override:
    return Override(
        target_ref=target,
        field="soft_decline_recovery_rate",
        previous_value="61% within 14 days",
        new_value="52% within 14 days",
        reason="Contoso dispute aside, the W09 cohort recovered at 52%; the driver is 19 days old",
        author="analyst@novatech",
        author_role=ApprovalRole.ANALYST,
        created_at=TS,
    )


def test_an_override_is_recorded_as_an_object_not_an_edit(ledger: ReviewLedger) -> None:
    recorded = ledger.record_override(override())

    assert ledger.overrides == [recorded]
    assert recorded.reason, "an override without a stated reason is just an edit"
    assert ledger.overrides_for("dodo:decline-2026-W10#soft_rate") == [recorded]


def test_the_preparer_cannot_publish_their_own_cycle(ledger: ReviewLedger) -> None:
    with pytest.raises(SegregationOfDutiesError, match="cannot also publish"):
        ledger.publish(
            version_id="fv-2026-W10",
            week_ending=date(2026, 3, 6),
            published_by="analyst@novatech",
            published_by_role=ApprovalRole.TREASURER,
        )


def test_an_analyst_cannot_publish_at_all(ledger: ReviewLedger) -> None:
    with pytest.raises(SegregationOfDutiesError, match="may not publish"):
        ledger.publish(
            version_id="fv-2026-W10",
            week_ending=date(2026, 3, 6),
            published_by="second.analyst@novatech",
            published_by_role=ApprovalRole.ANALYST,
        )


def test_publishing_locks_the_version_and_carries_the_overrides(ledger: ReviewLedger) -> None:
    recorded = ledger.record_override(override())

    published = ledger.publish(
        version_id="fv-2026-W10",
        week_ending=date(2026, 3, 6),
        published_by="treasurer@novatech",
        published_by_role=ApprovalRole.TREASURER,
        now=TS,
    )

    assert published.override_ids == [recorded.override_id]
    assert ledger.published is published
    with pytest.raises(RuntimeError, match="already published"):
        ledger.publish(
            version_id="fv-2026-W10",
            week_ending=date(2026, 3, 6),
            published_by="cfo@novatech",
            published_by_role=ApprovalRole.CFO,
        )


def test_an_override_after_publication_belongs_to_next_week() -> None:
    ledger = ReviewLedger(prepared_by="analyst@novatech")
    ledger.publish(
        version_id="fv-2026-W10",
        week_ending=date(2026, 3, 6),
        published_by="treasurer@novatech",
        published_by_role=ApprovalRole.TREASURER,
    )

    with pytest.raises(RuntimeError, match="next week's cycle"):
        ledger.record_override(override())


# --- the close calendar ---------------------------------------------------------------


def closed_february() -> CloseCalendar:
    return CloseCalendar(
        periods=[
            ClosePeriod(
                label="2026-02", starts_on=date(2026, 2, 1), ends_on=date(2026, 2, 28), closed=True
            ),
            ClosePeriod(label="2026-03", starts_on=date(2026, 3, 1), ends_on=date(2026, 3, 31)),
        ]
    )


def test_nothing_posts_into_a_closed_period() -> None:
    ledger = ReviewLedger(prepared_by="analyst@novatech", calendar=closed_february())

    with pytest.raises(ClosedPeriodError, match="2026-02"):
        ledger.record_override(override(), effective_on=date(2026, 2, 20))
    with pytest.raises(ClosedPeriodError, match="2026-02"):
        ledger.publish(
            version_id="fv-2026-W09",
            week_ending=date(2026, 2, 27),
            published_by="treasurer@novatech",
            published_by_role=ApprovalRole.TREASURER,
        )


def test_the_open_period_is_unaffected() -> None:
    ledger = ReviewLedger(prepared_by="analyst@novatech", calendar=closed_february())

    ledger.record_override(override(), effective_on=date(2026, 3, 2))
    published = ledger.publish(
        version_id="fv-2026-W10",
        week_ending=date(2026, 3, 6),
        published_by="treasurer@novatech",
        published_by_role=ApprovalRole.TREASURER,
    )
    assert published.version_id == "fv-2026-W10"


# --- step 10 --------------------------------------------------------------------------


async def test_the_policy_check_runs_against_the_published_version(cycle, ledger) -> None:
    result = await cycle.run()
    ledger.publish(
        version_id=result.forecast_version_id,
        week_ending=date(2026, 3, 6),
        published_by="treasurer@novatech",
        published_by_role=ApprovalRole.TREASURER,
    )

    check = await cycle.policy_check(result, ledger)

    assert check.escalate, "the seeded W6 trough is below the floor"
    breach = check.worst()
    assert breach is not None
    assert breach.constraint_id == "min-cash"
    assert breach.week_index == 6
    assert breach.observed_money == Money.from_major("18400000", "USD")


async def test_the_policy_check_refuses_to_run_against_a_draft(cycle, ledger) -> None:
    """A war room opened on an unpublished number is a war room nobody trusts."""
    result = await cycle.run()

    with pytest.raises(RuntimeError, match="publish the cycle"):
        await cycle.policy_check(result, ledger)


async def test_the_breach_becomes_a_specific_dated_quantified_incident(cycle, ledger) -> None:
    result = await cycle.run()
    ledger.publish(
        version_id=result.forecast_version_id,
        week_ending=date(2026, 3, 6),
        published_by="treasurer@novatech",
        published_by_role=ApprovalRole.TREASURER,
    )

    incident = (await cycle.policy_check(result, ledger)).incident()

    assert incident is not None
    assert "min-cash" in incident.trigger
    assert "W6" in incident.trigger
    assert incident.detected_on == AS_OF


async def test_constraints_we_cannot_observe_are_reported_unchecked(cycle, ledger) -> None:
    """'We did not look' and 'we looked and it was fine' are different facts."""
    result = await cycle.run()
    ledger.publish(
        version_id=result.forecast_version_id,
        week_ending=date(2026, 3, 6),
        published_by="treasurer@novatech",
        published_by_role=ApprovalRole.TREASURER,
    )

    check = await cycle.policy_check(result, ledger)

    assert "protected-payroll" in check.unchecked
    assert "max-supplier-delay" in check.unchecked
    assert "min-cash" in check.checked
    assert "max-revolver-util" in check.checked


async def test_a_utilization_inside_the_ceiling_does_not_escalate(cycle, ledger) -> None:
    result = await cycle.run()
    ledger.publish(
        version_id=result.forecast_version_id,
        week_ending=date(2026, 3, 6),
        published_by="treasurer@novatech",
        published_by_role=ApprovalRole.TREASURER,
    )

    check = await cycle.policy_check(result, ledger)

    assert "max-revolver-util" not in {v.constraint_id for v in check.violations}
