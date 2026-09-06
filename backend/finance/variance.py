"""The variance bridge — the artifact the Treasurer opens first.

`WORKFLOW.md` §5 specifies two bridges per cycle, and they answer different
questions:

1. **Forecast vs actual** for the week just closed — *did we forecast correctly?*
2. **Forecast vs prior forecast** for weeks 1-12 — *what changed in our view of
   the future, and why?*

The second is the one that answers "what caused the cash forecast to change",
and it is a routine weekly artifact rather than a crisis investigation. Note the
week alignment it requires: this cycle's week 1 is last cycle's week 2, so a
naive index-to-index comparison compares different calendar weeks and produces a
bridge that ties to nothing. The comparison here is by **week start date**.

Both bridges are **materiality-gated**. Only variances above threshold are
handed to an agent for explanation; everything else is bucketed as immaterial
with the count and total shown. Explaining a $4K variance signals you do not
think in materiality, and a treasurer notices.

A bridge always ties: opening cash plus the sum of the category variances equals
the closing variance, by construction. `assert_ties` is the test hook that keeps
it that way.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from backend.contracts.common import MoneyDTO
from backend.contracts.forecast import VarianceBridge, VarianceRow
from backend.finance.errors import PolicyError
from backend.finance.forecast import CATEGORIES, Forecast
from backend.finance.materiality import resolve_threshold, threshold_basis
from backend.finance.money import Money
from backend.finance.policy import TreasuryPolicy

#: Bucket label for everything below the materiality threshold. Not a category —
#: it is a summary row, and it is labelled so nobody mistakes it for one.
IMMATERIAL_LABEL = "immaterial (below threshold)"


@dataclass(frozen=True, slots=True)
class ActualWeek:
    """Settled cash for one closed week, classified to forecast categories.

    `by_category` need not name every category; anything absent is zero. That is
    the honest representation of a week in which no rent was paid.
    """

    week_start: date
    week_end: date
    opening_cash: Money
    by_category: dict[str, Money]

    def amount(self, category: str, currency: str) -> Money:
        return self.by_category.get(category, Money.zero(currency))

    def net_change(self, currency: str) -> Money:
        return Money.sum(self.by_category.values(), currency)

    def closing_cash(self, currency: str) -> Money:
        return self.opening_cash + self.net_change(currency)


@dataclass(frozen=True, slots=True)
class BridgeLine:
    """One category's contribution to the bridge, before DTO conversion."""

    category: str
    forecast: Money
    comparator: Money
    variance: Money
    material: bool
    explanation: str | None = None


def _gate(
    lines: tuple[BridgeLine, ...],
    threshold: Money,
    currency: str,
) -> tuple[tuple[BridgeLine, ...], BridgeLine | None]:
    """Split lines into material ones and a single immaterial summary row."""
    material = tuple(line for line in lines if line.material)
    immaterial = tuple(line for line in lines if not line.material)
    if not immaterial:
        return material, None
    summary = BridgeLine(
        category=IMMATERIAL_LABEL,
        forecast=Money.sum((line.forecast for line in immaterial), currency),
        comparator=Money.sum((line.comparator for line in immaterial), currency),
        variance=Money.sum((line.variance for line in immaterial), currency),
        material=False,
        explanation=(
            f"{len(immaterial)} categor{'y' if len(immaterial) == 1 else 'ies'} "
            f"below the {threshold} materiality threshold"
        ),
    )
    return material, summary


def _rows_actual(
    lines: tuple[BridgeLine, ...], summary: BridgeLine | None
) -> tuple[VarianceRow, ...]:
    ordered = list(lines) + ([summary] if summary is not None else [])
    return tuple(
        VarianceRow(
            category=line.category,
            forecast=MoneyDTO.from_money(line.forecast),
            actual=MoneyDTO.from_money(line.comparator),
            variance=MoneyDTO.from_money(line.variance),
            material=line.material,
            explanation=line.explanation,
        )
        for line in ordered
    )


def _rows_prior(
    lines: tuple[BridgeLine, ...], summary: BridgeLine | None
) -> tuple[VarianceRow, ...]:
    ordered = list(lines) + ([summary] if summary is not None else [])
    return tuple(
        VarianceRow(
            category=line.category,
            forecast=MoneyDTO.from_money(line.forecast),
            prior_forecast=MoneyDTO.from_money(line.comparator),
            variance=MoneyDTO.from_money(line.variance),
            material=line.material,
            explanation=line.explanation,
        )
        for line in ordered
    )


def actual_vs_forecast(
    forecast: Forecast,
    actual: ActualWeek,
    policy: TreasuryPolicy,
    monthly_opex: Money,
    *,
    explanations: dict[str, str] | None = None,
) -> VarianceBridge:
    """Bridge the closed week: what we said would happen against what did.

    `forecast` is the version that was *published before* the week began — not
    the current one. Comparing a week against a forecast built after the fact
    measures nothing, and it is the easiest way to produce flattering accuracy
    statistics without noticing.
    """
    currency = forecast.currency
    if actual.opening_cash.currency.code != currency:
        raise PolicyError("actual week currency does not match the forecast")
    if actual.week_start not in forecast.weeks:
        raise PolicyError(f"week {actual.week_start.isoformat()} is not in the forecast horizon")
    unknown = set(actual.by_category) - set(CATEGORIES)
    if unknown:
        raise PolicyError(f"actuals use unknown categories: {sorted(unknown)}")

    week_index = forecast.weeks.index(actual.week_start) + 1
    threshold = resolve_threshold(policy, monthly_opex)
    notes = explanations or {}

    lines: list[BridgeLine] = []
    for category in CATEGORIES:
        forecast_amount = forecast.cell(category, week_index).amount
        actual_amount = actual.amount(category, currency)
        variance = actual_amount - forecast_amount
        material = abs(variance) >= threshold
        lines.append(
            BridgeLine(
                category=category,
                forecast=forecast_amount,
                comparator=actual_amount,
                variance=variance,
                material=material,
                explanation=notes.get(category) if material else None,
            )
        )

    material_lines, summary = _gate(tuple(lines), threshold, currency)
    closing_variance = Money.sum((line.variance for line in lines), currency)
    return VarianceBridge(
        kind="forecast_vs_actual",
        week_ending=actual.week_end,
        rows=_rows_actual(material_lines, summary),
        closing_variance=MoneyDTO.from_money(closing_variance),
    )


def forecast_vs_prior(
    current: Forecast,
    prior: Forecast,
    policy: TreasuryPolicy,
    monthly_opex: Money,
    *,
    explanations: dict[str, str] | None = None,
) -> VarianceBridge:
    """Bridge this cycle's view of the future against last cycle's.

    Compared **by calendar week**, over the weeks the two versions share. After a
    normal roll that is the current version's weeks 1-12 against the prior
    version's weeks 2-13; comparing index to index would silently offset the
    whole bridge by a week.
    """
    if current.currency != prior.currency:
        raise PolicyError("cannot bridge forecasts in different currencies")
    currency = current.currency
    shared = tuple(week for week in current.weeks if week in prior.weeks)
    if not shared:
        raise PolicyError("the two forecast versions share no weeks")

    threshold = resolve_threshold(policy, monthly_opex)
    notes = explanations or {}
    current_index = {week: index for index, week in enumerate(current.weeks, start=1)}
    prior_index = {week: index for index, week in enumerate(prior.weeks, start=1)}

    lines: list[BridgeLine] = []
    for category in CATEGORIES:
        current_total = Money.sum(
            (current.cell(category, current_index[week]).amount for week in shared), currency
        )
        prior_total = Money.sum(
            (prior.cell(category, prior_index[week]).amount for week in shared), currency
        )
        variance = current_total - prior_total
        material = abs(variance) >= threshold
        lines.append(
            BridgeLine(
                category=category,
                forecast=current_total,
                comparator=prior_total,
                variance=variance,
                material=material,
                explanation=notes.get(category) if material else None,
            )
        )

    material_lines, summary = _gate(tuple(lines), threshold, currency)
    closing_variance = Money.sum((line.variance for line in lines), currency)
    return VarianceBridge(
        kind="forecast_vs_prior",
        # The end of the last week both versions cover, so the header states a
        # week ending rather than a week starting.
        week_ending=shared[-1] + timedelta(days=6),
        rows=_rows_prior(material_lines, summary),
        closing_variance=MoneyDTO.from_money(closing_variance),
    )


def assert_ties(bridge: VarianceBridge) -> None:
    """Every row must sum to the stated closing variance.

    A bridge that does not tie is worse than no bridge: it invites the treasurer
    to hunt for a difference the system created. This is asserted rather than
    reconciled — if the arithmetic disagrees, the defect is upstream.
    """
    if not bridge.rows:
        return
    currency = bridge.closing_variance.currency
    total = sum(row.variance.amount for row in bridge.rows)
    if total != bridge.closing_variance.amount:
        raise PolicyError(
            f"variance bridge does not tie: rows sum to {total} "
            f"but closing variance is {bridge.closing_variance.amount} {currency}"
        )


def material_rows(bridge: VarianceBridge) -> tuple[VarianceRow, ...]:
    """Rows an agent should be asked to explain — and only those."""
    return tuple(row for row in bridge.rows if row.material)


def render(bridge: VarianceBridge, policy: TreasuryPolicy, monthly_opex: Money) -> str:
    """The bridge as `WORKFLOW.md` §5 draws it."""
    comparator_label = "Actual" if bridge.kind == "forecast_vs_actual" else "Prior"
    header = f"{'':<32}{'Forecast':>14}{comparator_label:>14}{'Variance':>14}   Explanation"
    lines = [
        f"Week ending {bridge.week_ending.isoformat() if bridge.week_ending else '—'}",
        header,
        "-" * len(header),
    ]
    for row in bridge.rows:
        comparator = row.actual if row.actual is not None else row.prior_forecast
        comparator_text = comparator.to_money().to_major_string() if comparator else "—"
        lines.append(
            f"{row.category:<32}"
            f"{row.forecast.to_money().to_major_string():>14}"
            f"{comparator_text:>14}"
            f"{row.variance.to_money().to_major_string():>14}"
            f"   {row.explanation or ''}"
        )
    lines.append("-" * len(header))
    lines.append(
        f"{'Net variance':<32}{'':>14}{'':>14}"
        f"{bridge.closing_variance.to_money().to_major_string():>14}"
    )
    lines.append("")
    lines.append(
        f"Materiality: {resolve_threshold(policy, monthly_opex)} — "
        f"{threshold_basis(policy, monthly_opex)}"
    )
    return "\n".join(lines)


__all__ = [
    "IMMATERIAL_LABEL",
    "ActualWeek",
    "BridgeLine",
    "actual_vs_forecast",
    "assert_ties",
    "forecast_vs_prior",
    "material_rows",
    "render",
]
