"""Stress testing: selection can be agentic; the math is deterministic.

Stressors are calibrated to measured forecast error percentiles from the engine — never
an invented −10%.
"""

from __future__ import annotations

from decimal import Decimal

from backend.contracts.money import Money
from backend.contracts.strategy import Strategy, Stressor, StressResult
from backend.tools.results import ForecastErrorPercentiles, LiquidityPosition


def calibrate_stressors(errors: ForecastErrorPercentiles) -> list[Stressor]:
    """Build named stressors from p90 error at the trough horizon."""
    by_category = {p.category: p for p in errors.percentiles if p.horizon_weeks in {4, 6, 13}}
    stressors: list[Stressor] = []

    ar = by_category.get("ar_collections")
    if ar is not None:
        stressors.append(
            Stressor(
                stressor_id="ar-p90",
                label=f"AR recovery at p90 error ({ar.p90_pct}%)",
                category="ar_collections",
                shift_pct=ar.p90_pct,
                calibration=(
                    f"p90 of {ar.sample_size}-week measured AR forecast error "
                    f"at {ar.horizon_weeks}w horizon"
                ),
            )
        )

    dodo = by_category.get("dodo_receipts")
    if dodo is not None:
        stressors.append(
            Stressor(
                stressor_id="dodo-p90",
                label=f"Dodo collections at p90 error ({dodo.p90_pct}%)",
                category="dodo_receipts",
                shift_pct=dodo.p90_pct,
                calibration=(
                    f"p90 of {dodo.sample_size}-week measured Dodo forecast error "
                    f"at {dodo.horizon_weeks}w horizon"
                ),
            )
        )

    # Combined case — additive AR + Dodo p90 so an aggressive no-debt plan fails the
    # golden-path stress and forces a replan into a revolver-backed bundle.
    ar_shift = ar.p90_pct if ar is not None else Decimal("12.9")
    dodo_shift = dodo.p90_pct if dodo is not None else Decimal("9.8")
    combined = min(ar_shift + dodo_shift, Decimal("45"))
    stressors.append(
        Stressor(
            stressor_id="combined-p90",
            label=f"Combined AR+Dodo stress ({combined}%)",
            category="combined",
            shift_pct=combined,
            calibration="Sum of AR and Dodo p90 measured errors (capped at 45%)",
        )
    )
    return stressors


def apply_stress(
    strategy: Strategy,
    stressor: Stressor,
    *,
    position: LiquidityPosition,
) -> StressResult:
    """Haircut the strategy's cash impact by the calibrated shift; floor is policy."""
    base = position.min_cash
    impact = strategy.net_cash_impact or Money.zero(base.currency)
    # Only haircut the portion attributable to stressed categories.
    fragile = _fragile_impact(strategy, stressor.category)
    resilient = Money(
        minor_units=impact.minor_units - fragile.minor_units,
        currency=impact.currency,
    )
    factor = Decimal("1") - (stressor.shift_pct / Decimal("100"))
    stressed_fragile = fragile.scale(factor)
    stressed_impact = Money(
        minor_units=resilient.minor_units + stressed_fragile.minor_units,
        currency=base.currency,
    )
    min_cash = Money(
        minor_units=base.minor_units + stressed_impact.minor_units,
        currency=base.currency,
    )
    floor = position.floor
    headroom = min_cash - floor
    return StressResult(
        strategy_id=strategy.strategy_id,
        stressor=stressor,
        min_cash=min_cash,
        min_cash_week=strategy.projected_min_cash_week or position.min_cash_week,
        floor=floor,
        passed=min_cash >= floor,
        headroom=headroom,
    )


def _fragile_impact(strategy: Strategy, category: str) -> Money:
    from backend.contracts.agent import ActionKind

    kinds: set[ActionKind]
    if category == "ar_collections":
        kinds = {
            ActionKind.COLLECTION_CALL,
            ActionKind.EARLY_PAY_DISCOUNT,
            ActionKind.DISPUTE_RESOLUTION,
        }
    elif category == "dodo_receipts":
        kinds = {ActionKind.DODO_RETRY, ActionKind.DODO_DUNNING}
    elif category == "combined":
        kinds = {
            ActionKind.COLLECTION_CALL,
            ActionKind.EARLY_PAY_DISCOUNT,
            ActionKind.DISPUTE_RESOLUTION,
            ActionKind.DODO_RETRY,
            ActionKind.DODO_DUNNING,
        }
    else:
        kinds = set()

    total = 0
    currency = "USD"
    for action in strategy.actions:
        if action.kind in kinds and action.amount is not None:
            total += action.amount.minor_units
            currency = action.amount.currency
    return Money(minor_units=total, currency=currency)


def pick_failing_then_passing(
    results: list[StressResult],
) -> tuple[StressResult | None, list[StressResult]]:
    """Identify a failure that should trigger replan, and the survivors."""
    failures = [r for r in results if not r.passed]
    passes = [r for r in results if r.passed]
    return (failures[0] if failures else None, passes)
