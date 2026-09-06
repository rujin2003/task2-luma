"""Step 10 of the Monday cycle: the policy check that decides whether a war room opens.

This is deterministic on purpose and without exception. Whether a covenant is breached is
not a matter of judgement, and a model that "assessed liquidity risk as elevated" has told
a treasurer nothing they can act on or audit. What comes out of here is a specific, dated,
quantified condition -- `projected minimum cash W6 = 18.40M USD vs floor 20.00M USD` -- and
that string is what the war room opens on and what the Commander is briefed with.

Each constraint kind is checked against the observation the engine already computed, never
against something reconstructed here. The tool layer returns decisions; this module
compares them to thresholds and says which comparison failed.

A soft violation is scored down and flagged. A hard violation escalates. That distinction
lives in `TreasuryPolicy`, comes through the tool layer as `ConstraintSeverity`, and is not
second-guessed here.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from backend.agents.context import Incident
from backend.contracts.constraints import (
    Constraint,
    ConstraintKind,
    ConstraintSeverity,
    ConstraintViolation,
)
from backend.contracts.provenance import Evidence, SourceSystem
from backend.tools.results import (
    CovenantStatus,
    ForecastSummary,
    LiquidityPosition,
    PolicyConstraints,
)

# 30-day liquidity is four whole forecast weeks. Naming the number once keeps the check
# and the message it produces from drifting apart.
LIQUIDITY_HORIZON_WEEKS = 4


class PolicyCheck(BaseModel):
    """The verdict for one cycle: what breached, how badly, and what to open on."""

    model_config = ConfigDict(frozen=True)

    as_of: str
    violations: list[ConstraintViolation] = Field(default_factory=list)
    checked: list[str] = Field(default_factory=list)
    unchecked: dict[str, str] = Field(default_factory=dict)

    @property
    def hard_violations(self) -> list[ConstraintViolation]:
        return [v for v in self.violations if v.severity is ConstraintSeverity.HARD]

    @property
    def escalate(self) -> bool:
        """A soft breach is a flag on a report. A hard breach opens the war room."""
        return bool(self.hard_violations)

    def worst(self) -> ConstraintViolation | None:
        """The breach the investigation is named after: hard first, then deepest week."""
        candidates = self.hard_violations or self.violations
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda v: (
                v.severity is not ConstraintSeverity.HARD,
                v.week_index if v.week_index is not None else 99,
            ),
        )

    def incident(self) -> Incident | None:
        """The Context Pack brief every agent in the escalation is opened with."""
        breach = self.worst()
        if breach is None:
            return None
        return Incident(
            trigger=breach.display()[:200],
            detected_on=self.as_of,
            detail=breach.description[:400],
        )


def check_policy(
    *,
    as_of: str,
    policy: PolicyConstraints,
    position: LiquidityPosition,
    forecast: ForecastSummary,
    covenants: CovenantStatus | None = None,
) -> PolicyCheck:
    """Compare every constraint we can observe against the engine's own numbers."""
    violations: list[ConstraintViolation] = []
    checked: list[str] = []
    unchecked: dict[str, str] = {}

    for constraint in policy.constraints:
        breach = _check_one(constraint, position, forecast, covenants)
        if breach is _NOT_OBSERVABLE:
            # A constraint we cannot evaluate is reported as unchecked rather than
            # quietly passing. "We did not look" and "we looked and it was fine" are
            # different facts, and only one of them should reassure anyone.
            unchecked[constraint.constraint_id] = (
                f"no observation available for {constraint.kind.value}"
            )
            continue
        checked.append(constraint.constraint_id)
        if breach is not None:
            violations.append(breach)

    return PolicyCheck(as_of=as_of, violations=violations, checked=checked, unchecked=unchecked)


# Sentinel distinguishing "checked and passed" (None) from "could not be checked".
_NOT_OBSERVABLE = ConstraintViolation(
    constraint_id="__unobservable__",
    kind=ConstraintKind.MIN_CASH,
    severity=ConstraintSeverity.SOFT,
    description="sentinel",
    threshold_display="sentinel",
)


def _check_one(
    constraint: Constraint,
    position: LiquidityPosition,
    forecast: ForecastSummary,
    covenants: CovenantStatus | None,
) -> ConstraintViolation | None:
    match constraint.kind:
        case ConstraintKind.MIN_CASH:
            return _check_min_cash(constraint, forecast)
        case ConstraintKind.MIN_30D_LIQUIDITY:
            return _check_30d_liquidity(constraint, position, forecast)
        case ConstraintKind.MAX_REVOLVER_UTILIZATION:
            return _check_revolver(constraint, position)
        case ConstraintKind.COVENANT_RATIO:
            return _check_covenants(constraint, covenants)
        case _:
            # Protected classes, supplier delay, closed periods and approval gates are
            # properties of a *proposed action*, not of a forecast. They are enforced by
            # the constraint gate in `worklist.py`, and checking them here would report
            # a breach nobody has yet had the chance to commit.
            return _NOT_OBSERVABLE


def _check_min_cash(
    constraint: Constraint, forecast: ForecastSummary
) -> ConstraintViolation | None:
    floor = constraint.money_threshold
    if floor is None or not forecast.weeks:
        return _NOT_OBSERVABLE
    trough = min(forecast.weeks, key=lambda week: week.closing_cash.minor_units)
    if trough.closing_cash >= floor:
        return None
    return ConstraintViolation(
        constraint_id=constraint.constraint_id,
        kind=constraint.kind,
        severity=constraint.severity,
        description=(
            f"Projected closing cash of {trough.closing_cash} in week {trough.week_index} "
            f"(ending {trough.week_ending}) is below the {floor} minimum-cash floor"
        ),
        observed_money=trough.closing_cash,
        threshold_display=f"floor {floor}",
        week_index=trough.week_index,
        evidence=_policy_evidence(constraint, forecast.references),
    )


def _check_30d_liquidity(
    constraint: Constraint, position: LiquidityPosition, forecast: ForecastSummary
) -> ConstraintViolation | None:
    """Cash at the 30-day mark plus undrawn revolver -- available liquidity, not cash."""
    threshold = constraint.money_threshold
    if threshold is None or not forecast.weeks:
        return _NOT_OBSERVABLE
    horizon = [week for week in forecast.weeks if week.week_index <= LIQUIDITY_HORIZON_WEEKS]
    if not horizon:
        return _NOT_OBSERVABLE
    trough = min(horizon, key=lambda week: week.closing_cash.minor_units)
    available = trough.closing_cash + position.revolver_available
    if available >= threshold:
        return None
    return ConstraintViolation(
        constraint_id=constraint.constraint_id,
        kind=constraint.kind,
        severity=constraint.severity,
        description=(
            f"Available liquidity of {available} at week {trough.week_index} "
            f"({trough.closing_cash} cash plus {position.revolver_available} undrawn) "
            f"is below the {threshold} 30-day requirement"
        ),
        observed_money=available,
        threshold_display=f"minimum {threshold}",
        week_index=trough.week_index,
        evidence=_policy_evidence(constraint, position.references),
    )


def _check_revolver(
    constraint: Constraint, position: LiquidityPosition
) -> ConstraintViolation | None:
    ceiling = constraint.ratio_threshold
    if ceiling is None:
        return _NOT_OBSERVABLE
    # The tool reports utilization as a percentage; the covenant is written as a ratio.
    observed = position.revolver_utilization_pct / Decimal(100)
    if observed <= ceiling:
        return None
    return ConstraintViolation(
        constraint_id=constraint.constraint_id,
        kind=constraint.kind,
        severity=constraint.severity,
        description=(
            f"Revolver utilization of {position.revolver_utilization_pct}% is above the "
            f"{ceiling * 100}% ceiling"
        ),
        observed_ratio=observed,
        threshold_display=f"ceiling {ceiling}",
        evidence=_policy_evidence(constraint, position.references),
    )


def _check_covenants(
    constraint: Constraint, covenants: CovenantStatus | None
) -> ConstraintViolation | None:
    """The engine tests covenants on their real definition and date; we read the verdict."""
    if covenants is None or not covenants.covenants:
        return _NOT_OBSERVABLE
    breached = [row for row in covenants.covenants if row.breached]
    if not breached:
        return None
    row = min(breached, key=lambda covenant: covenant.headroom_pct)
    return ConstraintViolation(
        constraint_id=constraint.constraint_id,
        kind=constraint.kind,
        severity=constraint.severity,
        description=(
            f"{row.label} tested {row.observed_ratio} against {row.threshold_ratio} "
            f"on {row.tested_on}"
        ),
        observed_ratio=row.observed_ratio,
        threshold_display=f"threshold {row.threshold_ratio}",
        evidence=[
            Evidence(
                reference=row.reference,
                source=SourceSystem.DEBT,
                excerpt=f"{row.label}: {row.observed_ratio} vs {row.threshold_ratio}",
            )
        ],
    )


def _policy_evidence(constraint: Constraint, fallbacks: list[str]) -> list[Evidence]:
    """Cite the policy row the threshold came from, or the tool rows that observed it."""
    reference = constraint.source_ref or (fallbacks[0] if fallbacks else None)
    if reference is None:
        return []
    prefix = reference.split(":", 1)[0]
    source = SourceSystem(prefix) if prefix in set(SourceSystem) else SourceSystem.POLICY
    return [Evidence(reference=reference, source=source, excerpt=constraint.description[:280])]
