"""The contract invariants, asserted. These are the safety rules, not style preferences."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.contracts import (
    APPROVAL_CARD_FIELDS,
    ActionKind,
    AgentFinding,
    AgentRole,
    AgentRun,
    AgentStatus,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalRole,
    ApprovalState,
    Confidence,
    ConfidenceBand,
    Constraint,
    ConstraintKind,
    ConstraintSeverity,
    ConstraintViolation,
    Evidence,
    Money,
    Override,
    ProposedAction,
    Provenance,
    Recommendation,
    RejectedAction,
    SegregationOfDutiesError,
    SourceSystem,
    Strategy,
    Stressor,
    StressResult,
    WorklistItem,
    WorklistStatus,
    check_maker_checker,
    money_sum,
    parse_reference,
)

TS = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
USD = "USD"


# --- money ---------------------------------------------------------------------------


def test_money_allocation_loses_no_minor_units() -> None:
    parts = Money.from_major("10.00", USD).allocate([1, 1, 1])
    assert [p.minor_units for p in parts] == [334, 333, 333]
    assert money_sum(parts, USD) == Money.from_major("10.00", USD)


def test_money_refuses_to_mix_currencies() -> None:
    with pytest.raises(ValueError):
        Money.from_major("1", USD) + Money.from_major("1", "EUR")


# --- provenance and evidence ---------------------------------------------------------


def test_reference_round_trips_through_provenance() -> None:
    prov = Provenance(
        source_system=SourceSystem.AP_LEDGER,
        record_id="BILL-77",
        field="due_date",
        as_of=TS,
        retrieved_at=TS,
    )
    assert prov.reference == "ap_ledger:BILL-77#due_date"
    assert parse_reference(prov.reference) == ("ap_ledger", "BILL-77", "due_date")


def test_malformed_reference_is_rejected_at_parse_time() -> None:
    with pytest.raises(ValueError):
        parse_reference("no-source-here")


# --- findings ------------------------------------------------------------------------


def test_a_complete_finding_must_cite_evidence() -> None:
    with pytest.raises(ValidationError):
        AgentFinding(
            agent=AgentRole.VARIANCE,
            status=AgentStatus.COMPLETE,
            headline="Receipts $4.0M below plan",
        )


def test_rejecting_another_agent_requires_evidence() -> None:
    """Supplier Risk is adversarial by design, but it may not reject on a hunch."""
    with pytest.raises(ValidationError):
        AgentFinding(
            agent=AgentRole.SUPPLIER_RISK,
            status=AgentStatus.REFUSED,
            headline="Rejected $1.1M proposed AP deferral",
            rejects=["ap-defer-1"],
        )


def test_followup_requires_a_question(evidence: Evidence) -> None:
    with pytest.raises(ValidationError):
        AgentFinding(
            agent=AgentRole.VARIANCE,
            status=AgentStatus.COMPLETE,
            headline="Two sources disagree on the receipt date",
            evidence=[evidence],
            requires_followup=True,
        )


def test_digest_line_carries_conclusions_only(finding: AgentFinding) -> None:
    line = finding.digest_line()
    assert line.startswith("ar_collections ok |")
    assert finding.headline in line


def test_ap_deferral_must_state_a_delay() -> None:
    with pytest.raises(ValidationError):
        ProposedAction(kind=ActionKind.AP_DEFER, rationale="stretch the supplier")


# --- runs ----------------------------------------------------------------------------


def test_a_timed_out_run_records_why(finding: AgentFinding) -> None:
    with pytest.raises(ValidationError):
        AgentRun(agent=AgentRole.DODO_REVENUE, status=AgentStatus.TIMEOUT, started_at=TS)

    run = AgentRun(
        agent=AgentRole.DODO_REVENUE,
        status=AgentStatus.TIMEOUT,
        started_at=TS,
        failure_reason="no response within 30s",
    )
    assert run.usage.total == 0


def test_a_complete_run_carries_a_finding() -> None:
    with pytest.raises(ValidationError):
        AgentRun(agent=AgentRole.FORECAST, status=AgentStatus.COMPLETE, started_at=TS)


# --- confidence ----------------------------------------------------------------------


def test_empirical_confidence_reports_measured_error() -> None:
    c = Confidence.empirical(
        mape_pct=Decimal("8.4"), sample_size=26, horizon_weeks=6, rationale="26-week roll"
    )
    assert c.display() == "MAPE 8.4% at W6 over 26 weeks"


def test_qualitative_confidence_may_not_claim_measured_error() -> None:
    with pytest.raises(ValidationError):
        Confidence(
            basis="qualitative",  # type: ignore[arg-type]
            band=ConfidenceBand.LOW,
            mape_pct=Decimal("5"),
            rationale="gut feel dressed as measurement",
        )


# --- constraints ---------------------------------------------------------------------


def test_a_constraint_carries_exactly_one_threshold() -> None:
    with pytest.raises(ValidationError):
        Constraint(
            constraint_id="min-cash",
            kind=ConstraintKind.MIN_CASH,
            severity=ConstraintSeverity.HARD,
            description="minimum operating cash",
            money_threshold=Money.from_major("20000000", USD),
            days_threshold=30,
        )


def test_a_violation_displays_dated_and_quantified() -> None:
    v = ConstraintViolation(
        constraint_id="min-cash",
        kind=ConstraintKind.MIN_CASH,
        severity=ConstraintSeverity.HARD,
        description="minimum cash breached",
        observed_money=Money.from_major("18400000", USD),
        threshold_display="USD 20,000,000.00",
        week_index=6,
    )
    assert v.display() == "min-cash at W6: 18400000.00 USD vs USD 20,000,000.00"


# --- strategy and stress -------------------------------------------------------------


def _violation(severity: ConstraintSeverity) -> ConstraintViolation:
    return ConstraintViolation(
        constraint_id="protected-payroll",
        kind=ConstraintKind.PROTECTED_PAYMENT_CLASS,
        severity=severity,
        description="payroll is a protected payment class",
        threshold_display="never deferrable",
    )


def test_a_hard_violation_makes_a_strategy_infeasible() -> None:
    action = ProposedAction(kind=ActionKind.REVOLVER_DRAW, rationale="clear the W6 floor")
    soft = Strategy(
        strategy_id="s1",
        name="Draw and collect",
        actions=[action],
        constraint_violations=[_violation(ConstraintSeverity.SOFT)],
    )
    hard = soft.model_copy(update={"constraint_violations": [_violation(ConstraintSeverity.HARD)]})
    assert soft.feasible
    assert not hard.feasible


def test_stress_headroom_must_tie_to_the_floor() -> None:
    stressor = Stressor(
        stressor_id="ar-p90",
        label="AR at the 90th percentile of our own 26-week error",
        category="ar",
        shift_pct=Decimal("-12.5"),
    )
    kwargs = {
        "strategy_id": "s1",
        "stressor": stressor,
        "min_cash": Money.from_major("18000000", USD),
        "min_cash_week": 6,
        "floor": Money.from_major("20000000", USD),
    }
    result = StressResult(
        passed=False,
        headroom=Money.from_major("-2000000", USD),
        **kwargs,  # type: ignore[arg-type]
    )
    assert not result.passed

    with pytest.raises(ValidationError):
        StressResult(
            passed=True,
            headroom=Money.from_major("-2000000", USD),
            **kwargs,  # type: ignore[arg-type]
        )


def test_a_rejection_must_be_evidenced() -> None:
    action = ProposedAction(kind=ActionKind.AP_DEFER, rationale="stretch a supplier", delay_days=21)
    with pytest.raises(ValidationError):
        RejectedAction(action=action, rejected_by="supplier_risk", reason="sole-source vendor")

    assert RejectedAction(
        action=action,
        rejected_by="supplier_risk",
        reason="sole-source vendor, 3 late payments in 6 months",
        violation=_violation(ConstraintSeverity.HARD),
    )


def test_a_row_needing_approval_references_a_request() -> None:
    kwargs = {
        "seq": 1,
        "owner": "collections@novatech",
        "action": "Call Contoso AP on invoice 10482",
        "amount": Money.from_major("1200000", USD),
        "due_date": TS.date(),
        "status": WorklistStatus.NEEDS_APPROVAL,
    }
    with pytest.raises(ValidationError):
        WorklistItem(**kwargs)  # type: ignore[arg-type]
    assert WorklistItem(approval_request_id="req-1", **kwargs)  # type: ignore[arg-type]


def test_a_degraded_recommendation_always_requires_human_review() -> None:
    strategy = Strategy(
        strategy_id="s1",
        name="Draw and collect",
        actions=[ProposedAction(kind=ActionKind.REVOLVER_DRAW, rationale="clear the floor")],
    )
    with pytest.raises(ValidationError):
        Recommendation(recommendation_id="r1", selected_strategy=strategy, degraded=True)

    with pytest.raises(ValidationError):
        Recommendation(
            recommendation_id="r1",
            selected_strategy=strategy,
            degraded=True,
            degradation_reason="Dodo agent timed out",
        )


# --- approvals -----------------------------------------------------------------------


def test_the_approval_card_renders_all_eight_fields_in_order(
    approval_request: ApprovalRequest,
) -> None:
    assert tuple(approval_request.card()) == APPROVAL_CARD_FIELDS


def test_the_preparer_cannot_approve_their_own_request(
    approval_request: ApprovalRequest,
) -> None:
    decision = ApprovalDecision(
        request_id="req-1",
        decided_by="Analyst@NovaTech",
        decided_by_role=ApprovalRole.CFO,
        approved=True,
        decided_at=TS,
    )
    with pytest.raises(SegregationOfDutiesError):
        check_maker_checker(approval_request, decision)


def test_a_blocked_action_cannot_be_approved_at_all(
    approval_request: ApprovalRequest,
) -> None:
    blocked = approval_request.model_copy(update={"state": ApprovalState.BLOCKED})
    decision = ApprovalDecision(
        request_id="req-1",
        decided_by="cfo@novatech",
        decided_by_role=ApprovalRole.CFO,
        approved=True,
        decided_at=TS,
    )
    with pytest.raises(SegregationOfDutiesError):
        check_maker_checker(blocked, decision)


def test_a_rejection_states_a_reason() -> None:
    with pytest.raises(ValidationError):
        ApprovalDecision(
            request_id="req-1",
            decided_by="cfo@novatech",
            decided_by_role=ApprovalRole.CFO,
            approved=False,
            decided_at=TS,
        )


def test_an_override_must_change_something() -> None:
    with pytest.raises(ValidationError):
        Override(
            target_ref="forecast:W6#ar_receipts",
            field="ar_receipts",
            previous_value="4200000",
            new_value="4200000",
            reason="no change",
            author="treasurer@novatech",
            author_role=ApprovalRole.TREASURER,
            created_at=TS,
        )
