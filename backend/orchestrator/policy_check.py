"""Policy check that opens the war room on a specific, dated, quantified condition."""

from __future__ import annotations

from backend.contracts.constraints import (
    ConstraintKind,
    ConstraintSeverity,
    ConstraintViolation,
)
from backend.contracts.money import Money
from backend.contracts.provenance import Evidence, SourceSystem
from backend.tools.results import CovenantStatus, LiquidityPosition, PolicyConstraints


def check_liquidity_policy(
    position: LiquidityPosition,
    covenants: CovenantStatus,
    policy: PolicyConstraints,
) -> ConstraintViolation | None:
    """Return the first hard breach, or None when the cycle stays quiet."""
    floor_constraint = next(
        (c for c in policy.constraints if c.kind is ConstraintKind.MIN_CASH),
        None,
    )
    floor = floor_constraint.money_threshold if floor_constraint else position.floor

    if position.min_cash < floor:
        evidence = [
            Evidence(
                reference=ref,
                source=SourceSystem(ref.split(":", 1)[0])
                if ref.split(":", 1)[0] in set(SourceSystem)
                else SourceSystem.POLICY,
                excerpt=f"min cash {position.min_cash} vs floor {floor}",
            )
            for ref in position.references[:3]
        ] or [
            Evidence(
                reference="policy:treasury-policy#min_cash",
                source=SourceSystem.POLICY,
                excerpt=f"min cash {position.min_cash} vs floor {floor}",
            )
        ]
        return ConstraintViolation(
            constraint_id=floor_constraint.constraint_id if floor_constraint else "min_cash",
            kind=ConstraintKind.MIN_CASH,
            severity=ConstraintSeverity.HARD,
            description=(
                f"Projected minimum cash {position.min_cash} breaches the "
                f"{floor} floor at week {position.min_cash_week}"
            ),
            observed_money=position.min_cash,
            threshold_display=str(floor),
            week_index=position.min_cash_week,
            evidence=evidence,
        )

    for row in covenants.covenants:
        if not row.breached:
            continue
        return ConstraintViolation(
            constraint_id=row.covenant_id,
            kind=ConstraintKind.COVENANT_RATIO,
            severity=ConstraintSeverity.HARD,
            description=f"{row.label} breached: {row.observed_ratio} vs {row.threshold_ratio}",
            observed_ratio=row.observed_ratio,
            threshold_display=str(row.threshold_ratio),
            evidence=[
                Evidence(
                    reference=row.reference,
                    source=SourceSystem.DEBT,
                    excerpt=f"{row.label} {row.observed_ratio}/{row.threshold_ratio}",
                )
            ],
        )

    liquidity_constraint = next(
        (c for c in policy.constraints if c.kind is ConstraintKind.MIN_30D_LIQUIDITY),
        None,
    )
    if liquidity_constraint and liquidity_constraint.money_threshold is not None:
        # Approximate 30-day liquidity as cash today + near-term revolver headroom.
        available = Money(
            minor_units=position.cash_today.minor_units + position.revolver_available.minor_units,
            currency=position.cash_today.currency,
        )
        if available < liquidity_constraint.money_threshold:
            return ConstraintViolation(
                constraint_id=liquidity_constraint.constraint_id,
                kind=ConstraintKind.MIN_30D_LIQUIDITY,
                severity=ConstraintSeverity.HARD,
                description=(
                    f"30-day liquidity {available} below {liquidity_constraint.money_threshold}"
                ),
                observed_money=available,
                threshold_display=str(liquidity_constraint.money_threshold),
                evidence=[
                    Evidence(
                        reference=liquidity_constraint.source_ref
                        or "policy:treasury-policy#min_30d_liquidity",
                        source=SourceSystem.POLICY,
                        excerpt=f"30d liquidity {available}",
                    )
                ],
            )

    return None
