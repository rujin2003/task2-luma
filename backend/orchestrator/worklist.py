"""Levers in, worklists out -- and every step between them is deterministic code.

Agents propose levers. They do not compose bundles, price them, rank them or decide which
one is recommended, because all four of those are arithmetic and arithmetic is not what a
language model is for. What comes out is a worklist: rows with an owner, a counterparty, a
document reference, an amount, a date and a status. "Accelerate $2.6M of AR" is not
something anyone can approve or do; row 1 of a worklist is.

Three things this module is careful about.

**The constraint gate runs before pricing, not after.** A bundle that only clears the floor
because payroll slipped is not a cheaper bundle, it is not a bundle. Rejected levers are
kept with the reason and the evidence, because what was *not* recommended is the first
thing an experienced treasurer looks for.

**A conflict that was resolved against a lever removes it.** The AP deferral of BILL-8841
does not survive into a bundle merely because AP proposed it first; Supplier Risk's upheld
objection is a rejection with the resolution's own evidence attached.

**The weights are configuration and they are surfaced.** `config/scoring.yaml` is read at
composition time and travels with the score into the UI. A hidden weight vector is an
unauditable recommendation.

What this module deliberately does not do is invent a financing rate. The all-in cost of a
revolver draw lives behind `get_debt_capacity`, which is not yet part of the async tool
Protocol, so a composed draw carries an unpriced financing cost and says so. Scoring
penalises the draw on utilization -- a number the engine does observe -- rather than on a
rate nobody supplied.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field

from backend.contracts.agent import ActionKind, AgentFinding, AgentRole, ProposedAction
from backend.contracts.constraints import Constraint, ConstraintKind, ConstraintViolation
from backend.contracts.money import Money, money_sum
from backend.contracts.provenance import Evidence, SourceSystem
from backend.contracts.strategy import RejectedAction, Strategy, WorklistItem, WorklistStatus
from backend.orchestrator.conflicts import ResolvedConflict
from backend.tools.results import DeferralCandidates, LiquidityPosition, PolicyConstraints

SCORING_PATH = Path(__file__).resolve().parents[2] / "config" / "scoring.yaml"

# Who executes each kind of row. A worklist row without an owner is a wish.
OWNERS: dict[ActionKind, str] = {
    ActionKind.COLLECTION_CALL: "Collections",
    ActionKind.EARLY_PAY_DISCOUNT: "Collections",
    ActionKind.DISPUTE_RESOLUTION: "Collections",
    ActionKind.AP_DEFER: "AP Manager",
    ActionKind.AP_ACCELERATE: "AP Manager",
    ActionKind.DODO_RETRY: "Treasury",
    ActionKind.DODO_DUNNING: "Treasury",
    ActionKind.REVOLVER_DRAW: "Treasury",
    ActionKind.ASSUMPTION_REVIEW: "Treasury Analyst",
}

# Working days a row is given before it is late. Short enough that the next cycle's bridge
# shows whether it landed, which is what closes the loop on collection probability.
DEFAULT_DUE_DAYS: dict[ActionKind, int] = {
    ActionKind.COLLECTION_CALL: 6,
    ActionKind.EARLY_PAY_DISCOUNT: 7,
    ActionKind.DISPUTE_RESOLUTION: 12,
    ActionKind.AP_DEFER: 8,
    ActionKind.AP_ACCELERATE: 8,
    ActionKind.DODO_RETRY: 5,
    ActionKind.DODO_DUNNING: 5,
    ActionKind.REVOLVER_DRAW: 9,
    ActionKind.ASSUMPTION_REVIEW: 10,
}

# Levers that move cash the wrong way, or not at all, are excluded from a bundle's impact.
INFLOW_KINDS = frozenset(
    {
        ActionKind.COLLECTION_CALL,
        ActionKind.EARLY_PAY_DISCOUNT,
        ActionKind.DISPUTE_RESOLUTION,
        ActionKind.DODO_RETRY,
        ActionKind.DODO_DUNNING,
        ActionKind.AP_DEFER,
        ActionKind.REVOLVER_DRAW,
    }
)


class ScoringWeights(BaseModel):
    """`config/scoring.yaml`, loaded once and carried into the UI with the score."""

    model_config = ConfigDict(frozen=True)

    version: int
    weights: dict[str, Annotated[int, Field(ge=0)]]
    unscored: dict[str, str] = Field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.weights.values()) or 1

    def share(self, objective: str) -> Decimal:
        return Decimal(self.weights.get(objective, 0)) / Decimal(self.total)


def load_weights(path: Path | None = None) -> ScoringWeights:
    raw = yaml.safe_load((path or SCORING_PATH).read_text(encoding="utf-8"))
    return ScoringWeights.model_validate(raw)


class ObjectiveScore(BaseModel):
    """One dimension, scored 0-100, with the sentence that explains the number."""

    model_config = ConfigDict(frozen=True)

    objective: str
    score: Annotated[Decimal, Field(ge=0, le=100)]
    weight: Annotated[int, Field(ge=0)]
    basis: Annotated[str, Field(min_length=1, max_length=280)]


class ScoreCard(BaseModel):
    """A strategy's score, its components, and the objectives nobody could score."""

    model_config = ConfigDict(frozen=True)

    strategy_id: str
    total: Annotated[Decimal, Field(ge=0, le=100)]
    objectives: list[ObjectiveScore] = Field(default_factory=list)
    unscored: dict[str, str] = Field(default_factory=dict)


class Composition(BaseModel):
    """Everything the composition step produced: bundles, rows, refusals, scores."""

    model_config = ConfigDict(frozen=True)

    strategies: list[Strategy] = Field(default_factory=list)
    rejected: list[RejectedAction] = Field(default_factory=list)
    scores: dict[str, ScoreCard] = Field(default_factory=dict)
    shortfall: Money
    unpriced: list[str] = Field(default_factory=list)
    # The deferral rows this composition priced against. Carried rather than re-fetched,
    # so the worklist is rendered from the same rows the bundles were costed on.
    candidates: DeferralCandidates

    def ranked(self) -> list[Strategy]:
        """Feasible bundles, best first. An infeasible one is never ranked into first."""
        feasible = [s for s in self.strategies if s.feasible]
        return sorted(feasible, key=lambda s: self.scores[s.strategy_id].total, reverse=True)


# --- the constraint gate ---------------------------------------------------------------


def gate(
    actions: list[tuple[AgentRole, ProposedAction]],
    *,
    policy: PolicyConstraints,
    candidates: DeferralCandidates,
    position: LiquidityPosition,
    conflicts: list[ResolvedConflict],
) -> tuple[list[tuple[AgentRole, ProposedAction]], list[RejectedAction]]:
    """Filter infeasible levers before anything is priced, and say why each one went."""
    protected = {row.document_ref: row for row in candidates.rows if row.protected}
    terms = {row.document_ref: row for row in candidates.rows}
    overruled = {
        action_ref: resolved
        for resolved in conflicts
        if resolved.overruled is not None
        for action_ref in [resolved.conflict.subject]
    }
    protection = _constraint(policy, ConstraintKind.PROTECTED_PAYMENT_CLASS)
    delay_limit = _constraint(policy, ConstraintKind.MAX_SUPPLIER_DELAY)

    kept: list[tuple[AgentRole, ProposedAction]] = []
    rejected: list[RejectedAction] = []

    for role, action in actions:
        document = action.document_ref or ""

        if document in protected and protection is not None:
            row = protected[document]
            rejected.append(
                RejectedAction(
                    action=action,
                    rejected_by="constraint gate",
                    reason=(
                        f"{row.payment_class} is a protected payment class under "
                        f"{protection.constraint_id}. Protected classes are not levers."
                    ),
                    violation=_violation(protection, f"{document} is {row.payment_class}"),
                    evidence=[_evidence(row.reference, f"{row.supplier} {document}, {row.amount}")],
                )
            )
            continue

        resolved = overruled.get(document)
        if resolved is not None and resolved.overruled is role:
            rejected.append(
                RejectedAction(
                    action=action,
                    rejected_by=(resolved.upheld.value if resolved.upheld else "conflict"),
                    reason=resolved.resolution,
                    evidence=resolved.evidence,
                )
            )
            continue

        row = terms.get(document)
        if (
            action.kind is ActionKind.AP_DEFER
            and row is not None
            and action.delay_days is not None
            and action.delay_days > row.max_delay_days
        ):
            rejected.append(
                RejectedAction(
                    action=action,
                    rejected_by="constraint gate",
                    reason=(
                        f"A {action.delay_days}d deferral of {document} exceeds the "
                        f"{row.max_delay_days}d the terms allow"
                    ),
                    violation=_violation(
                        delay_limit,
                        f"{action.delay_days}d proposed against {row.max_delay_days}d of terms",
                        observed_days=action.delay_days,
                    )
                    if delay_limit is not None
                    else None,
                    evidence=[_evidence(row.reference, f"{row.supplier} {document}, {row.amount}")],
                )
            )
            continue

        if (
            action.kind is ActionKind.REVOLVER_DRAW
            and action.amount is not None
            and action.amount > position.revolver_available
        ):
            rejected.append(
                RejectedAction(
                    action=action,
                    rejected_by="constraint gate",
                    reason=(
                        f"A {action.amount} draw exceeds the {position.revolver_available} "
                        f"of undrawn capacity on the facility"
                    ),
                    evidence=[
                        _evidence(
                            position.references[0] if position.references else "policy:facility",
                            f"undrawn capacity {position.revolver_available}",
                        )
                    ],
                )
            )
            continue

        kept.append((role, action))

    return kept, rejected


# --- composition and pricing -----------------------------------------------------------


def compose(
    findings: list[AgentFinding],
    *,
    policy: PolicyConstraints,
    candidates: DeferralCandidates,
    position: LiquidityPosition,
    conflicts: list[ResolvedConflict] | None = None,
    weights: ScoringWeights | None = None,
    excluded_kinds: frozenset[ActionKind] = frozenset(),
) -> Composition:
    """Gate the levers, build four differently-shaped bundles, price and score them."""
    weights = weights or load_weights()
    shortfall = _shortfall(position)

    proposed = [
        (finding.agent, action)
        for finding in findings
        for action in finding.recommended_actions
        if action.kind not in excluded_kinds
    ]
    kept, rejected = gate(
        proposed,
        policy=policy,
        candidates=candidates,
        position=position,
        conflicts=conflicts or [],
    )

    receivables = [entry for entry in kept if entry[1].kind in _AR_KINDS]
    subscriptions = [entry for entry in kept if entry[1].kind in _DODO_KINDS]
    payables = [entry for entry in kept if entry[1].kind is ActionKind.AP_DEFER]

    # Four materially different risk shapes, not four permutations. Each one trades a
    # different thing away: nothing, supplier goodwill, revolver headroom, or coverage.
    bundles: list[list[tuple[AgentRole, ProposedAction]]] = [
        receivables + subscriptions,
        receivables + subscriptions + payables,
        receivables + subscriptions,  # the financing variant; the draw is added below
        receivables + payables,
    ]
    names = [
        ("receivables-first", "Receivables and subscription recovery only"),
        ("working-capital", "Working capital: receipts plus trade deferral"),
        ("financing-bridge", "Financing bridge: receipts plus a revolver draw"),
        ("no-subscription", "Receipts and deferral, holding subscription recovery back"),
    ]

    strategies: list[Strategy] = []
    scores: dict[str, ScoreCard] = {}
    unpriced: list[str] = []

    for (strategy_id, label), actions in zip(names, bundles, strict=True):
        composed = [action for _, action in actions]
        if strategy_id == "financing-bridge" and ActionKind.REVOLVER_DRAW not in excluded_kinds:
            draw = _compose_draw(composed, shortfall, position)
            if draw is not None:
                composed = [*composed, draw]
                unpriced.append(
                    "revolver draw: all-in cost is unpriced -- get_debt_capacity is not "
                    "behind the async tool layer yet, and a rate is never invented"
                )
        if not composed:
            continue

        impact = _net_impact(composed, candidates)
        strategy = Strategy(
            strategy_id=strategy_id,
            name=label,
            actions=composed,
            projected_min_cash=position.min_cash + impact,
            projected_min_cash_week=position.min_cash_week,
            net_cash_impact=impact,
            financing_cost=None,
            constraint_violations=[],
        )
        strategies.append(strategy)
        scores[strategy_id] = score(
            strategy, position=position, candidates=candidates, weights=weights
        )

    return Composition(
        strategies=strategies,
        rejected=rejected,
        scores=scores,
        shortfall=shortfall,
        unpriced=sorted(set(unpriced)),
        candidates=candidates,
    )


_AR_KINDS = frozenset(
    {
        ActionKind.COLLECTION_CALL,
        ActionKind.EARLY_PAY_DISCOUNT,
        ActionKind.DISPUTE_RESOLUTION,
    }
)
_DODO_KINDS = frozenset({ActionKind.DODO_RETRY, ActionKind.DODO_DUNNING})


def _shortfall(position: LiquidityPosition) -> Money:
    """How far the trough is below the floor. Zero when there is no breach."""
    gap = position.floor - position.min_cash
    return gap if gap.minor_units > 0 else Money.zero(position.floor.currency)


def _compose_draw(
    actions: list[ProposedAction], shortfall: Money, position: LiquidityPosition
) -> ProposedAction | None:
    """The lever no agent proposes. Code sizes it to the residual gap, and no larger.

    Drawing more than the gap is not prudence, it is interest on money we do not need,
    and it spends headroom that exists for the next surprise.
    """
    if shortfall.is_zero():
        return None
    residual = shortfall - money_sum(
        [_impact(action, {}, shortfall.currency) for action in actions], shortfall.currency
    )
    if residual.minor_units <= 0:
        return None
    amount = min(residual, position.revolver_available, key=lambda m: m.minor_units)
    if amount.minor_units <= 0:
        return None
    return ProposedAction(
        kind=ActionKind.REVOLVER_DRAW,
        rationale=(
            f"Residual gap of {residual} after the operating levers; sized to the gap "
            f"rather than to the facility"
        ),
        counterparty="Lender",
        amount=amount,
        evidence_refs=list(position.references[:1]),
    )


def _net_impact(actions: list[ProposedAction], candidates: DeferralCandidates) -> Money:
    """Cash the bundle raises inside the horizon, with discount forgone netted off."""
    discounts = {
        row.document_ref: row.discount_forgone
        for row in candidates.rows
        if row.discount_forgone is not None
    }
    currency = candidates.total_deferrable.currency
    return money_sum([_impact(action, discounts, currency) for action in actions], currency)


def _impact(action: ProposedAction, discounts: dict[str, Money], currency: str = "USD") -> Money:
    if action.amount is None or action.kind not in INFLOW_KINDS:
        return Money.zero(currency)
    amount = action.amount
    forgone = discounts.get(action.document_ref or "")
    if action.kind is ActionKind.AP_DEFER and forgone is not None:
        # Deferring a bill under 2/10 net 30 is not free liquidity; it is borrowing at a
        # rate most treasurers would refuse if it were quoted to them. So it is quoted.
        amount = amount - forgone
    return amount


# --- scoring ---------------------------------------------------------------------------


def score(
    strategy: Strategy,
    *,
    position: LiquidityPosition,
    candidates: DeferralCandidates,
    weights: ScoringWeights,
) -> ScoreCard:
    """Deterministic, weighted, and every component carries the sentence behind it."""
    shortfall = _shortfall(position)
    impact = strategy.net_cash_impact or Money.zero(position.floor.currency)
    sole_source = {row.document_ref for row in candidates.rows if row.max_delay_days <= 7}

    objectives = [
        _closed(shortfall, impact, weights),
        _financing(strategy, position, weights),
        _supplier(strategy, candidates, sole_source, weights),
        _customer(strategy, weights),
        _covenant(strategy, position, weights),
        _operational(strategy, weights),
    ]
    total = sum(
        (objective.score * weights.share(objective.objective) for objective in objectives),
        Decimal(0),
    )
    return ScoreCard(
        strategy_id=strategy.strategy_id,
        total=min(Decimal(100), max(Decimal(0), total)).quantize(Decimal("0.1")),
        objectives=objectives,
        unscored=dict(weights.unscored),
    )


def _pct(numerator: int, denominator: int) -> Decimal:
    if denominator <= 0:
        return Decimal(100)
    return min(Decimal(100), Decimal(max(numerator, 0)) * 100 / Decimal(denominator))


def _closed(shortfall: Money, impact: Money, weights: ScoringWeights) -> ObjectiveScore:
    covered = _pct(impact.minor_units, shortfall.minor_units)
    return ObjectiveScore(
        objective="shortfall_closed",
        score=covered,
        weight=weights.weights.get("shortfall_closed", 0),
        basis=f"{impact} raised against a {shortfall} gap ({covered:.0f}% covered)",
    )


def _financing(
    strategy: Strategy, position: LiquidityPosition, weights: ScoringWeights
) -> ObjectiveScore:
    drawn = money_sum(
        [
            action.amount
            for action in strategy.actions
            if action.kind is ActionKind.REVOLVER_DRAW and action.amount is not None
        ],
        position.floor.currency,
    )
    available = position.revolver_available.minor_units or 1
    used = _pct(drawn.minor_units, available)
    return ObjectiveScore(
        objective="financing_load",
        score=Decimal(100) - used,
        weight=weights.weights.get("financing_load", 0),
        basis=(
            f"{drawn} drawn of {position.revolver_available} undrawn "
            f"({used:.0f}% of remaining capacity)"
        ),
    )


def _supplier(
    strategy: Strategy,
    candidates: DeferralCandidates,
    sole_source: set[str],
    weights: ScoringWeights,
) -> ObjectiveScore:
    deferrals = [a for a in strategy.actions if a.kind is ActionKind.AP_DEFER]
    terms = {row.document_ref: row.max_delay_days for row in candidates.rows}
    stretch = Decimal(0)
    for action in deferrals:
        allowed = terms.get(action.document_ref or "", 30) or 1
        stretch += Decimal(action.delay_days or 0) * 100 / Decimal(allowed)
        if action.document_ref in sole_source:
            stretch += Decimal(50)
    penalty = min(Decimal(100), stretch / Decimal(2))
    return ObjectiveScore(
        objective="supplier_disruption",
        score=Decimal(100) - penalty,
        weight=weights.weights.get("supplier_disruption", 0),
        basis=(
            f"{len(deferrals)} deferral(s), scored on how far each runs against its own terms"
            if deferrals
            else "no supplier is stretched by this bundle"
        ),
    )


def _customer(strategy: Strategy, weights: ScoringWeights) -> ObjectiveScore:
    disputes = [a for a in strategy.actions if a.kind is ActionKind.DISPUTE_RESOLUTION]
    calls = [a for a in strategy.actions if a.kind is ActionKind.COLLECTION_CALL]
    penalty = min(Decimal(100), Decimal(len(disputes)) * 20 + Decimal(len(calls)) * 5)
    return ObjectiveScore(
        objective="customer_relationship",
        score=Decimal(100) - penalty,
        weight=weights.weights.get("customer_relationship", 0),
        basis=(
            f"{len(calls)} collection call(s) and {len(disputes)} dispute escalation(s); "
            "a dispute pressed hard is a collection today and a renewal risk next year"
        ),
    )


def _covenant(
    strategy: Strategy, position: LiquidityPosition, weights: ScoringWeights
) -> ObjectiveScore:
    """Distance from the utilization ceiling once the bundle executes.

    Utilization after the draw is derived from what the engine already reported -- the
    facility limit falls out of undrawn capacity and current utilization -- so no rate or
    limit is invented here.
    """
    drawn_now_pct = position.revolver_utilization_pct
    draw = money_sum(
        [
            action.amount
            for action in strategy.actions
            if action.kind is ActionKind.REVOLVER_DRAW and action.amount is not None
        ],
        position.floor.currency,
    )
    if drawn_now_pct >= Decimal(100):
        limit_minor = position.revolver_available.minor_units or 1
    else:
        limit_minor = (
            int(
                Decimal(position.revolver_available.minor_units)
                / ((Decimal(100) - drawn_now_pct) / Decimal(100))
            )
            or 1
        )
    after = drawn_now_pct + _pct(draw.minor_units, limit_minor)
    return ObjectiveScore(
        objective="covenant_headroom",
        score=max(Decimal(0), Decimal(100) - after),
        weight=weights.weights.get("covenant_headroom", 0),
        basis=f"utilization moves from {drawn_now_pct}% to {after:.1f}% after this bundle",
    )


def _operational(strategy: Strategy, weights: ScoringWeights) -> ObjectiveScore:
    rows = len(strategy.actions)
    penalty = min(Decimal(100), Decimal(max(rows - 2, 0)) * 12)
    return ObjectiveScore(
        objective="operational_risk",
        score=Decimal(100) - penalty,
        weight=weights.weights.get("operational_risk", 0),
        basis=f"{rows} row(s) a human has to execute inside the week",
    )


# --- the worklist ----------------------------------------------------------------------


def to_worklist(
    strategy: Strategy,
    *,
    as_of: date,
    findings: list[AgentFinding],
    candidates: DeferralCandidates,
) -> list[WorklistItem]:
    """Turn the chosen bundle into rows a named person can execute this week."""
    proposer = {
        action.document_ref or action.kind.value: finding.agent
        for finding in findings
        for action in finding.recommended_actions
    }
    cited = {evidence.reference: evidence for finding in findings for evidence in finding.evidence}
    discounts = {
        row.document_ref: row.discount_forgone
        for row in candidates.rows
        if row.discount_forgone is not None
    }
    strategy_currency = candidates.total_deferrable.currency
    rows: list[WorklistItem] = []
    for seq, action in enumerate(_ordered(strategy.actions), start=1):
        key = action.document_ref or action.kind.value
        # `probability_pct` is deliberately left unset. The AR tool returned
        # probability-weighted expected amounts, so the weight behind a row is not
        # recoverable here, and a made-up probability is worse than a blank column.
        rows.append(
            WorklistItem(
                seq=seq,
                owner=OWNERS[action.kind],
                action=_describe(action, discounts),
                counterparty=action.counterparty,
                document_ref=action.document_ref,
                amount=action.amount or Money.zero(strategy_currency),
                due_date=action.due_by or as_of + timedelta(days=DEFAULT_DUE_DAYS[action.kind]),
                status=WorklistStatus.OPEN,
                proposed_by=proposer.get(key),
                expected_cash_impact=_impact(action, discounts, strategy_currency),
                evidence=[cited[ref] for ref in action.evidence_refs if ref in cited],
            )
        )
    return rows


def _ordered(actions: list[ProposedAction]) -> list[ProposedAction]:
    """Largest cash impact first, so the row that matters is row 1."""
    return sorted(actions, key=lambda a: -(a.amount.minor_units if a.amount else 0))


def _describe(action: ProposedAction, discounts: dict[str, Money]) -> str:
    document = action.document_ref or ""
    match action.kind:
        case ActionKind.COLLECTION_CALL:
            text = f"Call {action.counterparty} re: {document}"
        case ActionKind.DISPUTE_RESOLUTION:
            text = f"Resolve the dispute on {document} with {action.counterparty}"
        case ActionKind.EARLY_PAY_DISCOUNT:
            text = f"Offer early-pay terms on {document} to {action.counterparty}"
        case ActionKind.AP_DEFER:
            forgone = discounts.get(document)
            cost = f", forgoing {forgone} of discount" if forgone else ""
            text = f"Defer {document} ({action.counterparty}) by {action.delay_days}d{cost}"
        case ActionKind.AP_ACCELERATE:
            text = f"Pay {document} ({action.counterparty}) early"
        case ActionKind.DODO_RETRY:
            text = "Retry the soft-declined Dodo cohort inside its documented window"
        case ActionKind.DODO_DUNNING:
            text = "Run credential-update dunning on the expired-card cohort"
        case ActionKind.REVOLVER_DRAW:
            text = (
                f"Draw {action.amount} on the revolver "
                "(all-in cost unpriced: get_debt_capacity is not behind the tool layer)"
            )
        case _:
            text = action.rationale
    return text[:200]


# --- helpers ---------------------------------------------------------------------------


def _constraint(policy: PolicyConstraints, kind: ConstraintKind) -> Constraint | None:
    return next((c for c in policy.constraints if c.kind is kind), None)


def _violation(
    constraint: Constraint | None, description: str, *, observed_days: int | None = None
) -> ConstraintViolation | None:
    if constraint is None:
        return None
    return ConstraintViolation(
        constraint_id=constraint.constraint_id,
        kind=constraint.kind,
        severity=constraint.severity,
        description=description[:400],
        observed_days=observed_days,
        threshold_display=(
            f"{constraint.days_threshold}d"
            if constraint.days_threshold is not None
            else constraint.description
        ),
    )


def _evidence(reference: str, excerpt: str) -> Evidence:
    prefix = reference.split(":", 1)[0]
    source = SourceSystem(prefix) if prefix in set(SourceSystem) else SourceSystem.POLICY
    return Evidence(reference=reference, source=source, excerpt=excerpt[:280])
