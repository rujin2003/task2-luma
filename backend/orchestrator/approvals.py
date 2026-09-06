"""Approvals: nothing consequential happens silently, and nothing is waved through.

Four controls, and each one is code rather than convention because each one is the kind of
thing that gets skipped under time pressure.

**Risk classification is deterministic rules, never a model.** Reversibility, magnitude and
counterparty impact decide whether a row is `auto_safe`, `requires_approval` or `blocked`.
Asking a language model whether an action is risky would put the one judgement nobody can
audit at the centre of the one place it matters most.

**Routing is a matrix, not a boolean.** `config/doa_matrix.yaml` bands by action class and
amount, and `backend/finance/controls.py::route_approval` selects the unique band. An
action $10 over a band routes one level higher, because that is what a band means.

**A blocked route has no approver.** Payroll and statutory tax are not a very high
threshold; they are outside the matrix. A constraint a sufficiently senior person can wave
through is not a constraint.

**Execution is gated and dry-run by default.** The adapter refuses to execute a row without
a recorded, matching, maker-checker-clean approval, and the always-gated classes stay gated
whatever the configuration says. `AuditLog` records who approved, when, what they saw, and
which data snapshot they saw it against -- append-only, because an audit trail you can
amend is a log.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field

from backend.contracts.agent import ActionKind
from backend.contracts.approvals import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalRole,
    ApprovalRoute,
    ApprovalState,
    SegregationOfDutiesError,
    check_maker_checker,
)
from backend.contracts.money import Money
from backend.contracts.provenance import Evidence
from backend.contracts.strategy import (
    Recommendation,
    RejectedAction,
    Strategy,
    WorklistItem,
    WorklistStatus,
)
from backend.finance.controls import ApprovalRequiredError, route_approval
from backend.tools.results import DeferralCandidates

DOA_PATH = Path(__file__).resolve().parents[2] / "config" / "doa_matrix.yaml"

# The discount rate above which an early-pay offer stops being a discount and starts being
# a financing decision. 2% for 20 days is roughly 36% annualised.
EXPENSIVE_DISCOUNT_PCT = Decimal("2")

# Classes that stay approval-gated regardless of configuration. A deployment that could
# switch these off would be a deployment where the control does not exist.
ALWAYS_GATED: frozenset[ActionKind] = frozenset(
    {ActionKind.REVOLVER_DRAW, ActionKind.AP_DEFER, ActionKind.EARLY_PAY_DISCOUNT}
)

# Reversible, cheap, and visible to one counterparty at a time.
AUTO_SAFE_KINDS: frozenset[ActionKind] = frozenset(
    {ActionKind.COLLECTION_CALL, ActionKind.DODO_RETRY, ActionKind.DODO_DUNNING}
)


class RiskClass(StrEnum):
    AUTO_SAFE = "auto_safe"
    REQUIRES_APPROVAL = "requires_approval"
    BLOCKED = "blocked"


class RiskVerdict(BaseModel):
    """The classification and the sentence behind it. Both are shown."""

    model_config = ConfigDict(frozen=True)

    risk: RiskClass
    reversibility: Annotated[str, Field(min_length=1, max_length=120)]
    magnitude: Annotated[str, Field(min_length=1, max_length=120)]
    counterparty_impact: Annotated[str, Field(min_length=1, max_length=160)]
    basis: Annotated[str, Field(min_length=1, max_length=400)]


class DoaMatrix(BaseModel):
    """`config/doa_matrix.yaml`, validated into the frozen `ApprovalRoute` contract."""

    model_config = ConfigDict(frozen=True)

    version: int
    routes: list[ApprovalRoute] = Field(min_length=1)

    def route_for(self, action_class: str, amount: Money) -> ApprovalRoute:
        """The unique band. Overlapping or missing bands raise rather than guess."""
        return route_approval(action_class, amount.minor_units, self.routes)


def load_matrix(path: Path | None = None) -> DoaMatrix:
    raw = yaml.safe_load((path or DOA_PATH).read_text(encoding="utf-8"))
    return DoaMatrix.model_validate(raw)


# --- classification ---------------------------------------------------------------------


def action_class(item: WorklistItem, *, protected: bool = False) -> str:
    """The matrix key for a row. Protected classes route to the blocked entry."""
    if protected:
        return "protected_payment_class"
    kind = _kind_of(item)
    if kind is ActionKind.EARLY_PAY_DISCOUNT:
        # Unknown rate routes to the expensive band. Approval never routes down on a
        # missing fact.
        return "early_pay_discount_le_2pct" if item.probability_pct is None else ""
    return kind.value


def classify(
    item: WorklistItem,
    *,
    candidates: DeferralCandidates,
    discount_pct: Decimal | None = None,
) -> RiskVerdict:
    """Deterministic rules over reversibility, magnitude and counterparty impact."""
    kind = _kind_of(item)
    protected = {row.document_ref: row for row in candidates.rows if row.protected}

    if item.document_ref in protected:
        row = protected[item.document_ref]
        return RiskVerdict(
            risk=RiskClass.BLOCKED,
            reversibility="irreversible within the period",
            magnitude=str(item.amount),
            counterparty_impact=f"{row.payment_class}: employees or a tax authority",
            basis=(
                f"{item.document_ref} is a {row.payment_class} payment. Protected classes "
                f"are outside the delegation matrix entirely, not at the top of it."
            ),
        )

    if kind in AUTO_SAFE_KINDS:
        return RiskVerdict(
            risk=RiskClass.AUTO_SAFE,
            reversibility="reversible: a call or a retry can simply not be repeated",
            magnitude=str(item.amount),
            counterparty_impact="one counterparty, no contractual change",
            basis=(
                f"A {kind.value.replace('_', ' ')} moves no cash by itself and changes no "
                f"terms. It is executed and reported, not approved."
            ),
        )

    reasons = []
    if kind is ActionKind.REVOLVER_DRAW:
        reasons.append("a draw is visible to the lender and cannot be unwound quietly")
    if kind is ActionKind.AP_DEFER:
        reasons.append("a deferral changes what a supplier was told to expect")
    if discount_pct is not None and discount_pct > EXPENSIVE_DISCOUNT_PCT:
        reasons.append(f"a {discount_pct}% discount is a financing decision, not a discount")
    return RiskVerdict(
        risk=RiskClass.REQUIRES_APPROVAL,
        reversibility="not reversible without telling the counterparty",
        magnitude=str(item.amount),
        counterparty_impact=item.counterparty or "external counterparty",
        basis="; ".join(reasons) or f"{kind.value} carries external commitment",
    )


def _kind_of(item: WorklistItem) -> ActionKind:
    """Recover the lever kind from the row that was rendered from it."""
    text = item.action.lower()
    if text.startswith("draw "):
        return ActionKind.REVOLVER_DRAW
    if text.startswith("defer "):
        return ActionKind.AP_DEFER
    if text.startswith("pay "):
        return ActionKind.AP_ACCELERATE
    if text.startswith("call "):
        return ActionKind.COLLECTION_CALL
    if text.startswith("resolve the dispute"):
        return ActionKind.DISPUTE_RESOLUTION
    if text.startswith("offer early-pay"):
        return ActionKind.EARLY_PAY_DISCOUNT
    if "dunning" in text:
        return ActionKind.DODO_DUNNING
    if "retry" in text:
        return ActionKind.DODO_RETRY
    return ActionKind.ASSUMPTION_REVIEW


# --- the approval card --------------------------------------------------------------------


class ApprovalPack(BaseModel):
    """The rows, the cards they need, and the rows that need none."""

    model_config = ConfigDict(frozen=True)

    worklist: list[WorklistItem] = Field(default_factory=list)
    requests: list[ApprovalRequest] = Field(default_factory=list)
    verdicts: dict[int, RiskVerdict] = Field(default_factory=dict)
    blocked: list[RejectedAction] = Field(default_factory=list)

    def request_for(self, seq: int) -> ApprovalRequest | None:
        return next((r for r in self.requests if r.worklist_seq == seq), None)


def prepare(
    recommendation: Recommendation,
    *,
    candidates: DeferralCandidates,
    matrix: DoaMatrix,
    prepared_by: str,
    now: datetime | None = None,
    data_snapshot_ref: str | None = None,
) -> ApprovalPack:
    """Classify every row, route the ones that need it, and build their cards."""
    created_at = now or datetime.now(UTC)
    discounts = {
        row.document_ref: _discount_pct(row.discount_forgone, row.amount)
        for row in candidates.rows
        if row.discount_forgone is not None
    }
    protected = {row.document_ref for row in candidates.rows if row.protected}

    rows: list[WorklistItem] = []
    requests: list[ApprovalRequest] = []
    verdicts: dict[int, RiskVerdict] = {}

    for item in recommendation.worklist:
        verdict = classify(
            item, candidates=candidates, discount_pct=discounts.get(item.document_ref or "")
        )
        verdicts[item.seq] = verdict

        if verdict.risk is RiskClass.AUTO_SAFE:
            rows.append(item.model_copy(update={"status": WorklistStatus.QUEUED}))
            continue

        route = matrix.route_for(
            _matrix_key(item, discounts, protected), item.amount
        )
        request = _card(
            item,
            verdict=verdict,
            route=route,
            recommendation=recommendation,
            prepared_by=prepared_by,
            created_at=created_at,
            data_snapshot_ref=data_snapshot_ref,
        )
        requests.append(request)
        rows.append(
            item.model_copy(
                update={
                    "status": (
                        WorklistStatus.REJECTED
                        if verdict.risk is RiskClass.BLOCKED
                        else WorklistStatus.NEEDS_APPROVAL
                    ),
                    "approval_request_id": request.request_id,
                }
            )
        )

    return ApprovalPack(
        worklist=rows,
        requests=requests,
        verdicts=verdicts,
        blocked=list(recommendation.rejected_actions),
    )


def _matrix_key(
    item: WorklistItem, discounts: dict[str, Decimal], protected: set[str]
) -> str:
    if item.document_ref in protected:
        return "protected_payment_class"
    kind = _kind_of(item)
    if kind is ActionKind.EARLY_PAY_DISCOUNT:
        rate = discounts.get(item.document_ref or "")
        # A missing rate routes to the expensive band: approval never routes down on a
        # fact nobody supplied.
        if rate is None or rate > EXPENSIVE_DISCOUNT_PCT:
            return "early_pay_discount_gt_2pct"
        return "early_pay_discount_le_2pct"
    return kind.value


def _discount_pct(forgone: Money, amount: Money) -> Decimal:
    if amount.minor_units == 0:
        return Decimal(0)
    return (Decimal(forgone.minor_units) * 100 / Decimal(amount.minor_units)).quantize(
        Decimal("0.01")
    )


def _card(
    item: WorklistItem,
    *,
    verdict: RiskVerdict,
    route: ApprovalRoute,
    recommendation: Recommendation,
    prepared_by: str,
    created_at: datetime,
    data_snapshot_ref: str | None,
) -> ApprovalRequest:
    """The eight fields, in the order a treasurer reads them before signing."""
    impact = item.expected_cash_impact or item.amount
    strategy: Strategy = recommendation.selected_strategy
    return ApprovalRequest(
        worklist_seq=item.seq,
        recommendation_id=recommendation.recommendation_id,
        action=item.action,
        amount=item.amount,
        expected_impact=(
            f"{impact} toward the shortfall; the bundle projects "
            f"{strategy.projected_min_cash} at W{strategy.projected_min_cash_week}"
        )[:400],
        risk=f"{verdict.reversibility}; {verdict.counterparty_impact}"[:400],
        evidence=item.evidence or [_uncited(item)],
        why_recommended=(
            f"Part of {strategy.name}, proposed by "
            f"{item.proposed_by.value if item.proposed_by else 'the composition step'}. "
            f"{recommendation.summary}"
        )[:600],
        what_could_go_wrong=_downside(item, verdict, recommendation)[:600],
        approval_required=route.accountable or ApprovalRole.CFO,
        route=route,
        prepared_by=prepared_by,
        state=ApprovalState.BLOCKED if route.blocked else ApprovalState.PENDING,
        created_at=created_at,
        data_snapshot_ref=data_snapshot_ref,
    )


def _downside(
    item: WorklistItem, verdict: RiskVerdict, recommendation: Recommendation
) -> str:
    """The field most systems leave blank. Sourced from the stress run, not invented."""
    if verdict.risk is RiskClass.BLOCKED:
        return verdict.basis
    failed = [r for r in recommendation.stress_results if not r.passed]
    parts = [verdict.basis]
    if failed:
        worst = min(failed, key=lambda r: r.headroom.minor_units)
        parts.append(
            f"Under {worst.stressor.label} ({worst.stressor.shift_pct}%) an earlier bundle "
            f"fell {abs(worst.headroom)} short of the floor; this row's contribution is "
            f"subject to the same error."
        )
    else:
        parts.append(
            "The bundle survived every calibrated stressor, so the residual risk is "
            "execution: a row that does not land leaves the gap open."
        )
    return " ".join(parts)


def _uncited(item: WorklistItem) -> Evidence:
    """A card must cite something. A row composed by code cites the row it came from."""
    from backend.contracts.provenance import SourceSystem

    return Evidence(
        reference=f"forecast:{item.document_ref or item.action[:40]}",
        source=SourceSystem.FORECAST,
        excerpt="Composed by the recommendation engine; no single ledger row underlies it.",
    )


# --- deciding, and the audit trail --------------------------------------------------------


class AuditEntry(BaseModel):
    """One immutable line: who, when, what they saw, against which snapshot."""

    model_config = ConfigDict(frozen=True)

    at: datetime
    actor: str
    actor_role: ApprovalRole
    request_id: str
    action: str
    amount: Money
    approved: bool
    reason: str = ""
    card_seen: dict[str, str] = Field(default_factory=dict)
    data_snapshot_ref: str | None = None
    app_version: str


class AuditLog:
    """Append-only. An audit trail you can amend is a log."""

    def __init__(self, app_version: str) -> None:
        self.app_version = app_version
        self._entries: list[AuditEntry] = []

    @property
    def entries(self) -> list[AuditEntry]:
        return list(self._entries)

    def record(self, request: ApprovalRequest, decision: ApprovalDecision) -> AuditEntry:
        entry = AuditEntry(
            at=decision.decided_at,
            actor=decision.decided_by,
            actor_role=decision.decided_by_role,
            request_id=request.request_id,
            action=request.action,
            amount=request.amount,
            approved=decision.approved,
            reason=decision.reason,
            # What they saw, not what the record says now. The card is snapshotted at the
            # moment of decision so a later edit upstream cannot rewrite what was signed.
            card_seen=request.card(),
            data_snapshot_ref=decision.data_snapshot_ref or request.data_snapshot_ref,
            app_version=self.app_version,
        )
        self._entries.append(entry)
        return entry


class ApprovalLedger:
    """Holds pending requests and the decisions taken against them."""

    def __init__(self, *, audit: AuditLog) -> None:
        self.audit = audit
        self._requests: dict[str, ApprovalRequest] = {}
        self._decisions: dict[str, ApprovalDecision] = {}

    def register(self, requests: list[ApprovalRequest]) -> None:
        for request in requests:
            self._requests[request.request_id] = request

    def get(self, request_id: str) -> ApprovalRequest | None:
        return self._requests.get(request_id)

    def decision_for(self, request_id: str) -> ApprovalDecision | None:
        return self._decisions.get(request_id)

    def decide(self, decision: ApprovalDecision) -> AuditEntry:
        """Apply a decision. Wrong role, wrong person and blocked routes all raise."""
        request = self._requests.get(decision.request_id)
        if request is None:
            raise ApprovalRequiredError(f"no approval request {decision.request_id}")
        if decision.request_id in self._decisions:
            raise ApprovalRequiredError(
                f"{decision.request_id} has already been decided; a second decision would "
                "overwrite the first, and the first is the one that was signed"
            )

        # Preparer is not approver. Raises `SegregationOfDutiesError`, and blocked routes
        # raise here too rather than being approvable by anyone at all.
        check_maker_checker(request, decision)

        if decision.approved and decision.decided_by_role is not request.approval_required:
            raise SegregationOfDutiesError(
                f"{decision.decided_by_role.value} may not approve this: the matrix routes "
                f"it to {request.approval_required.value}"
            )

        self._decisions[decision.request_id] = decision
        return self.audit.record(request, decision)

    def state_of(self, request_id: str) -> ApprovalState:
        request = self._requests.get(request_id)
        if request is None:
            return ApprovalState.DRAFT
        if request.state is ApprovalState.BLOCKED:
            return ApprovalState.BLOCKED
        decision = self._decisions.get(request_id)
        if decision is None:
            return ApprovalState.PENDING
        return ApprovalState.APPROVED if decision.approved else ApprovalState.REJECTED


# --- execution ----------------------------------------------------------------------------


class ExecutionAdapter(Protocol):
    """Where a row actually happens. Everything ships behind this, dry-run by default."""

    dry_run: bool

    async def execute(self, item: WorklistItem) -> str: ...


class DryRunAdapter:
    """Records what it would have done. The default, and what the demo runs on."""

    def __init__(self) -> None:
        self.dry_run = True
        self.executed: list[WorklistItem] = []

    async def execute(self, item: WorklistItem) -> str:
        self.executed.append(item)
        return f"dry run: would execute row {item.seq} -- {item.action}"


class Executor:
    """The gate. No approved, matching, maker-checker-clean decision, no execution."""

    def __init__(self, *, ledger: ApprovalLedger, adapter: ExecutionAdapter) -> None:
        self._ledger = ledger
        self._adapter = adapter

    async def execute(self, item: WorklistItem) -> str:
        kind = _kind_of(item)

        if item.status is WorklistStatus.REJECTED:
            raise ApprovalRequiredError(
                f"row {item.seq} was rejected and cannot be executed: {item.action}"
            )

        if item.approval_request_id is None:
            if kind in ALWAYS_GATED:
                # The configuration cannot switch this off. A deployment where it could
                # is a deployment where the control does not exist.
                raise ApprovalRequiredError(
                    f"{kind.value} always requires a recorded approval, whatever the "
                    f"configuration says; row {item.seq} carries none"
                )
            return await self._adapter.execute(item)

        state = self._ledger.state_of(item.approval_request_id)
        if state is not ApprovalState.APPROVED:
            raise ApprovalRequiredError(
                f"row {item.seq} is {state.value}, not approved: {item.action}"
            )
        return await self._adapter.execute(item)
