"""Approvals: classification, routing, maker-checker and the execution gate.

These assert the four controls that are meant to be code rather than convention, and each
test is written to fail loudly if the control is quietly relaxed:

* a protected class has **no** approver, rather than a very senior one;
* the band is selected on the amount, so a row just over one routes a level higher;
* the preparer cannot sign their own card, and neither can the wrong role;
* the executor refuses an always-gated row that carries no recorded approval.

The worklist under test is the one the golden investigation actually produces, so a change
to composition that quietly drops evidence off a row shows up here too.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from backend.agents.replay import ReplayProvider
from backend.contracts import AgentRole, ApprovalRole, Money, WorklistStatus
from backend.contracts.approvals import (
    APPROVAL_CARD_FIELDS,
    ApprovalDecision,
    ApprovalState,
    SegregationOfDutiesError,
)
from backend.contracts.provenance import Evidence, SourceSystem
from backend.contracts.strategy import WorklistItem
from backend.finance.controls import ApprovalRequiredError
from backend.orchestrator.approvals import (
    ApprovalLedger,
    AuditLog,
    DryRunAdapter,
    Executor,
    RiskClass,
    action_class,
    classify,
    load_matrix,
    prepare,
)
from backend.orchestrator.investigation import Investigation
from backend.orchestrator.policy import check_policy
from backend.orchestrator.runs import InMemoryRunStore

AS_OF = date(2026, 3, 2)
NOW = datetime(2026, 3, 2, 9, 30, tzinfo=UTC)
PREPARER = "analyst@novatech"
APP_VERSION = "test"


@pytest.fixture
def matrix():
    return load_matrix()


@pytest.fixture
async def candidates(toolset):
    return await toolset.rank_deferral_candidates()


@pytest.fixture
async def recommendation(bus, toolset, routing):
    check = check_policy(
        as_of="2026-03-02",
        policy=await toolset.get_policy_constraints(),
        position=await toolset.get_liquidity_position(),
        forecast=await toolset.get_forecast_summary(),
        covenants=await toolset.get_covenant_status(),
    )
    investigation = Investigation(
        provider=ReplayProvider(),
        toolset=toolset,
        routing=routing,
        bus=bus,
        company="NovaTech Industries",
        as_of=AS_OF,
        check=check,
        store=InMemoryRunStore(),
        investigation_id="inv-approvals",
    )
    result = await investigation.run()
    assert result.recommendation is not None
    return result.recommendation


@pytest.fixture
def pack(recommendation, candidates, matrix):
    return prepare(
        recommendation,
        candidates=candidates,
        matrix=matrix,
        prepared_by=PREPARER,
        now=NOW,
        data_snapshot_ref="fv-2026-W10",
    )


def _row(**overrides) -> WorklistItem:
    """A worklist row shaped like the ones composition emits."""
    base = {
        "seq": 1,
        "owner": "ap@novatech",
        "action": "Defer BILL-8863 (Globex Logistics) by 30d",
        "counterparty": "Globex Logistics",
        "document_ref": "BILL-8863",
        "amount": Money.from_major("620000", "USD"),
        "due_date": date(2026, 3, 26),
        "status": WorklistStatus.OPEN,
        "proposed_by": AgentRole.AP_OPTIMIZATION,
        "evidence": [
            Evidence(
                reference="ap_ledger:BILL-8863#due_date",
                source=SourceSystem.AP_LEDGER,
                excerpt="Globex Logistics, bill 8863, $620K, due 2026-03-26, net 45",
            )
        ],
    }
    return WorklistItem(**{**base, **overrides})


# --- classification -------------------------------------------------------------------


async def test_a_protected_class_is_blocked_rather_than_escalated(candidates) -> None:
    """Payroll is outside the matrix, not at the top of it."""
    payroll = _row(
        action="Defer PAY-2026-W12 (NovaTech Payroll) by 14d",
        counterparty="NovaTech Payroll",
        document_ref="PAY-2026-W12",
        amount=Money.from_major("2450000", "USD"),
    )

    verdict = classify(payroll, candidates=candidates)

    assert verdict.risk is RiskClass.BLOCKED
    assert "payroll" in verdict.counterparty_impact.lower()


async def test_reversible_levers_are_auto_safe_and_never_reach_a_card(candidates) -> None:
    call = _row(action="Call Fabrikam Inc re: INV-10517", document_ref="INV-10517")

    assert classify(call, candidates=candidates).risk is RiskClass.AUTO_SAFE


async def test_a_deferral_requires_approval_and_says_why(candidates) -> None:
    verdict = classify(_row(), candidates=candidates)

    assert verdict.risk is RiskClass.REQUIRES_APPROVAL
    assert "supplier" in verdict.basis.lower()


async def test_an_expensive_discount_is_named_as_a_financing_decision(candidates) -> None:
    offer = _row(action="Offer early-pay on BILL-8841", document_ref="BILL-8841")

    verdict = classify(offer, candidates=candidates, discount_pct=Decimal("2.50"))

    assert verdict.risk is RiskClass.REQUIRES_APPROVAL
    assert "financing decision" in verdict.basis


# --- routing --------------------------------------------------------------------------


def test_early_pay_with_no_stated_rate_routes_to_the_expensive_band() -> None:
    """Approval never routes *down* on a fact nobody supplied."""
    offer = _row(action="Offer early-pay on BILL-8877", document_ref="BILL-8877")

    assert action_class(offer, discount_pct=None) == "early_pay_discount_gt_2pct"
    assert action_class(offer, discount_pct=Decimal("2.00")) == "early_pay_discount_le_2pct"
    assert action_class(offer, discount_pct=Decimal("2.01")) == "early_pay_discount_gt_2pct"


def test_a_protected_row_routes_to_the_blocked_entry() -> None:
    assert action_class(_row(), protected=True) == "protected_payment_class"


def test_a_row_just_over_a_band_routes_one_level_higher(matrix) -> None:
    """That is what a band means; rounding it down would defeat the matrix."""
    under = matrix.route_for("ap_defer", Money.from_major("249999.99", "USD"))
    over = matrix.route_for("ap_defer", Money.from_major("250000", "USD"))

    assert under.accountable is ApprovalRole.TREASURER
    assert over.accountable is ApprovalRole.CFO


def test_the_blocked_route_names_no_approver(matrix) -> None:
    route = matrix.route_for("protected_payment_class", Money.from_major("2450000", "USD"))

    assert route.blocked
    assert route.accountable is None
    assert "no delegation level" in route.blocked_reason.lower()


# --- the card -------------------------------------------------------------------------


def test_auto_safe_rows_are_queued_without_a_card(pack) -> None:
    calls = [row for row in pack.worklist if row.action.startswith("Call ")]

    assert calls, "the golden worklist contains a collection call"
    assert all(row.status is WorklistStatus.QUEUED for row in calls)
    assert all(pack.request_for(row.seq) is None for row in calls)


def test_the_deferral_is_gated_and_carries_its_request_id(pack) -> None:
    defer = next(row for row in pack.worklist if row.action.startswith("Defer "))

    assert defer.status is WorklistStatus.NEEDS_APPROVAL
    assert defer.approval_request_id is not None
    assert pack.request_for(defer.seq) is not None


def test_the_card_renders_exactly_the_eight_fields_in_order(pack) -> None:
    request = pack.requests[0]

    assert tuple(request.card()) == APPROVAL_CARD_FIELDS
    assert all(value for value in request.card().values()), "no field renders blank"


def test_the_620k_deferral_routes_to_the_cfo(pack) -> None:
    """$620K sits in the $250K-$1M band, which the table sends to the CFO."""
    defer = next(row for row in pack.worklist if row.action.startswith("Defer "))
    request = pack.request_for(defer.seq)

    assert request.approval_required is ApprovalRole.CFO
    assert request.route is not None
    assert request.route.reviewer is ApprovalRole.TREASURER


def test_what_could_go_wrong_is_sourced_from_the_stress_run(pack, recommendation) -> None:
    """The field most systems leave blank, and it is not invented."""
    request = pack.requests[0]

    assert request.what_could_go_wrong
    if any(not result.passed for result in recommendation.stress_results):
        assert "short of the floor" in request.what_could_go_wrong
    else:
        assert "survived every calibrated stressor" in request.what_could_go_wrong


def test_a_row_composed_by_code_still_cites_something(pack) -> None:
    """The golden worklist has an uncited row; a card with no evidence is not shippable."""
    assert all(request.evidence for request in pack.requests)


def test_rejected_actions_travel_onto_the_pack(pack, recommendation) -> None:
    assert pack.blocked == recommendation.rejected_actions
    assert pack.blocked, "supplier risk refuses a lever on the golden path"


# --- deciding -------------------------------------------------------------------------


@pytest.fixture
def ledger(pack):
    log = ApprovalLedger(audit=AuditLog(APP_VERSION))
    log.register(pack.requests)
    return log


def _decision(request, *, by="cfo@novatech", role=ApprovalRole.CFO, approved=True, reason=""):
    return ApprovalDecision(
        request_id=request.request_id,
        decided_by=by,
        decided_by_role=role,
        approved=approved,
        reason=reason or ("" if approved else "not this week"),
        decided_at=NOW,
    )


def test_the_preparer_cannot_approve_their_own_card(ledger, pack) -> None:
    request = pack.requests[0]

    with pytest.raises(SegregationOfDutiesError, match="cannot also approve"):
        ledger.decide(_decision(request, by=PREPARER, role=request.approval_required))


def test_the_wrong_role_cannot_approve(ledger, pack) -> None:
    request = next(r for r in pack.requests if r.approval_required is ApprovalRole.CFO)

    with pytest.raises(SegregationOfDutiesError, match="routes"):
        ledger.decide(_decision(request, by="t@novatech", role=ApprovalRole.TREASURER))


def test_a_decision_cannot_be_overwritten(ledger, pack) -> None:
    """The first decision is the one that was signed."""
    request = next(r for r in pack.requests if r.approval_required is ApprovalRole.CFO)
    ledger.decide(_decision(request))

    with pytest.raises(ApprovalRequiredError, match="already been decided"):
        ledger.decide(_decision(request, approved=False, reason="changed my mind"))


def test_an_unknown_request_is_refused(ledger, pack) -> None:
    stranger = pack.requests[0].model_copy(update={"request_id": "req-does-not-exist"})

    with pytest.raises(ApprovalRequiredError, match="no approval request"):
        ledger.decide(_decision(stranger))


def test_a_rejection_is_recorded_as_a_rejection(ledger, pack) -> None:
    request = next(r for r in pack.requests if r.approval_required is ApprovalRole.CFO)
    ledger.decide(_decision(request, approved=False, reason="Acme relationship is fragile"))

    assert ledger.state_of(request.request_id) is ApprovalState.REJECTED


# --- the audit trail ------------------------------------------------------------------


def test_the_audit_entry_snapshots_the_card_that_was_signed(ledger, pack) -> None:
    """A later edit upstream must not be able to rewrite what the approver saw."""
    request = next(r for r in pack.requests if r.approval_required is ApprovalRole.CFO)

    entry = ledger.decide(_decision(request))

    assert entry.card_seen == request.card()
    assert entry.actor == "cfo@novatech"
    assert entry.data_snapshot_ref == "fv-2026-W10"
    assert entry.app_version == APP_VERSION


def test_the_audit_log_is_append_only(ledger, pack) -> None:
    request = next(r for r in pack.requests if r.approval_required is ApprovalRole.CFO)
    ledger.decide(_decision(request))

    ledger.audit.entries.clear()  # a copy, not the log

    assert len(ledger.audit.entries) == 1


# --- the execution gate ---------------------------------------------------------------


@pytest.fixture
def executor(ledger):
    return Executor(ledger=ledger, adapter=DryRunAdapter())


async def test_an_always_gated_row_with_no_approval_is_refused(executor) -> None:
    """The configuration cannot switch this off."""
    with pytest.raises(ApprovalRequiredError, match="always requires a recorded approval"):
        await executor.execute(_row(approval_request_id=None))


async def test_a_pending_row_does_not_execute(executor, pack) -> None:
    defer = next(row for row in pack.worklist if row.status is WorklistStatus.NEEDS_APPROVAL)

    with pytest.raises(ApprovalRequiredError, match="pending, not approved"):
        await executor.execute(defer)


async def test_a_rejected_row_does_not_execute(executor) -> None:
    with pytest.raises(ApprovalRequiredError, match="was rejected"):
        await executor.execute(_row(status=WorklistStatus.REJECTED))


async def test_an_approved_row_executes_dry_run(executor, ledger, pack) -> None:
    defer = next(row for row in pack.worklist if row.status is WorklistStatus.NEEDS_APPROVAL)
    request = ledger.get(defer.approval_request_id)
    ledger.decide(_decision(request, role=request.approval_required))

    receipt = await executor.execute(defer)

    assert receipt.startswith("dry run:")


async def test_an_auto_safe_row_executes_without_an_approval(executor) -> None:
    call = _row(action="Call Fabrikam Inc re: INV-10517", document_ref="INV-10517")

    assert "dry run" in await executor.execute(call)
