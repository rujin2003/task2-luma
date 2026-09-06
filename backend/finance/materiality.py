"""Materiality threshold resolution.

Finance people think in materiality. Explaining a $4K variance signals you do not
(`WORKFLOW.md` §5), so a single resolver is used by variance decomposition,
exception surfacing and review routing alike — three consumers sharing one
threshold, rather than three hard-coded numbers that drift apart.

The convention is the standard one: **the lesser of** a fixed floor and a
percentage of monthly operating expense. A fast-growing company's percentage
overtakes the floor and the floor stops binding; a shrinking one is protected
from a threshold that scales away to nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.finance.errors import PolicyError
from backend.finance.money import Money
from backend.finance.policy import TreasuryPolicy


@dataclass(frozen=True, slots=True)
class MaterialityDecision:
    """Why an item was or was not material — surfaced, not just applied."""

    threshold: Money
    amount: Money
    material: bool
    basis: str

    def __str__(self) -> str:
        verdict = "material" if self.material else "immaterial"
        return f"{self.amount} is {verdict} against {self.threshold} ({self.basis})"


def resolve_threshold(policy: TreasuryPolicy, monthly_opex: Money) -> Money:
    """The effective materiality threshold for this company, this cycle."""
    return policy.materiality.resolve(monthly_opex)


def threshold_basis(policy: TreasuryPolicy, monthly_opex: Money) -> str:
    """Human-readable statement of which limb of the rule bound.

    The bridge shows this next to the immaterial bucket; a treasurer who cannot
    see why a $60K variance went unexplained will not trust the ones that were.
    """
    absolute = policy.materiality.absolute.to_money()
    percent_minor = monthly_opex.amount * policy.materiality.pct_of_monthly_opex_bps // 10_000
    bps = policy.materiality.pct_of_monthly_opex_bps
    if percent_minor < absolute.amount:
        return f"{bps} bps of monthly opex {monthly_opex} (below the {absolute} floor)"
    return f"absolute floor {absolute} (below {bps} bps of monthly opex {monthly_opex})"


def assess(policy: TreasuryPolicy, monthly_opex: Money, amount: Money) -> MaterialityDecision:
    """Classify one amount. Sign is irrelevant; magnitude is what matters."""
    threshold = resolve_threshold(policy, monthly_opex)
    if amount.currency.code != threshold.currency.code:
        raise PolicyError(
            f"cannot assess {amount.currency.code} against a "
            f"{threshold.currency.code} materiality threshold"
        )
    return MaterialityDecision(
        threshold=threshold,
        amount=amount,
        material=abs(amount) >= threshold,
        basis=threshold_basis(policy, monthly_opex),
    )


def is_material(policy: TreasuryPolicy, monthly_opex: Money, amount: Money) -> bool:
    return assess(policy, monthly_opex, amount).material


__all__ = [
    "MaterialityDecision",
    "assess",
    "is_material",
    "resolve_threshold",
    "threshold_basis",
]
