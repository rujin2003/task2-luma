"""Variance bridge tests.

`PHASES.md` requires "the variance bridge for a known week ties to zero". Two
things are checked here that are easy to get subtly wrong and hard to notice: the
bridge must **tie**, and the forecast-vs-prior comparison must align by
**calendar week** rather than by week index, because after a roll this cycle's
week 1 is last cycle's week 2.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from backend.contracts.common import MoneyDTO
from backend.contracts.forecast import VarianceBridge
from backend.finance import cadence, forecast, variance
from backend.finance.cadence import PayrollFrequency
from backend.finance.errors import PolicyError
from backend.finance.forecast import (
    CollectionCurve,
    ForecastInputs,
    LagComponent,
    OpenInvoice,
    PayrollPlan,
)
from backend.finance.money import Money
from backend.finance.policy import TreasuryPolicy
from backend.finance.variance import ActualWeek

CAL = cadence.us_calendar(2025, 2028)
AS_OF = datetime(2026, 9, 7, tzinfo=UTC)
FIRST_WEEK = date(2026, 9, 7)
POLICY = TreasuryPolicy.load()
# 100 bps of $6,000,000 monthly opex is $60,000, so the $50,000 absolute floor
# binds. Chosen so a $900,000 variance is material and a $40,000 one is not.
MONTHLY_OPEX = Money.of(600_000_000, "USD")

FLAT_CURVE = CollectionCurve(
    lag_mixture={"default": (LagComponent(10_000, 0),)},
    collection_probability_bps=dict.fromkeys(forecast.AGING_BUCKETS, 10_000),
)


def usd(major: int) -> Money:
    return Money.of(major * 100, "USD")


def build(first_week: date = FIRST_WEEK, **overrides: object) -> forecast.Forecast:
    defaults: dict[str, object] = {
        "version_id": "fv-1",
        "as_of": AS_OF,
        "first_week_start": first_week,
        "opening_cash": usd(20_000_000),
        "calendar": CAL,
        "undrawn_revolver": usd(25_000_000),
        "collection_curve": FLAT_CURVE,
        "open_invoices": (OpenInvoice("INV-1832", "Customer A", usd(4_200_000), date(2026, 9, 9)),),
        "payroll": PayrollPlan(
            salaried_gross_per_period=usd(2_100_000),
            hourly_gross_per_period=usd(0),
            frequency=PayrollFrequency.SEMI_MONTHLY,
        ),
        "rent_per_month": usd(300_000),
    }
    defaults.update(overrides)
    return forecast.build(ForecastInputs(**defaults))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Forecast vs actual
# --------------------------------------------------------------------------- #


def test_a_perfect_week_produces_a_bridge_that_ties_to_zero() -> None:
    result = build()
    actual = ActualWeek(
        week_start=FIRST_WEEK,
        week_end=FIRST_WEEK + timedelta(days=6),
        opening_cash=usd(20_000_000),
        by_category={
            "receipts_trade_ar": result.cell("receipts_trade_ar", 1).amount,
        },
    )
    bridge = variance.actual_vs_forecast(result, actual, POLICY, MONTHLY_OPEX)
    variance.assert_ties(bridge)
    assert bridge.closing_variance.amount == 0
    assert bridge.kind == "forecast_vs_actual"
    assert bridge.week_ending == date(2026, 9, 13)
    # Nothing was material, so the whole grid collapses to one immaterial row.
    assert [row.category for row in bridge.rows] == [variance.IMMATERIAL_LABEL]


def test_a_material_slip_is_explained_and_the_rest_is_bucketed() -> None:
    """The `WORKFLOW.md` §5 story: $900K slips to next week."""
    result = build()
    forecast_ar = result.cell("receipts_trade_ar", 1).amount
    actual = ActualWeek(
        week_start=FIRST_WEEK,
        week_end=FIRST_WEEK + timedelta(days=6),
        opening_cash=usd(20_000_000),
        by_category={
            "receipts_trade_ar": forecast_ar - usd(900_000),
            "rent_leases": usd(-40_000),  # immaterial
        },
    )
    bridge = variance.actual_vs_forecast(
        result,
        actual,
        POLICY,
        MONTHLY_OPEX,
        explanations={
            "receipts_trade_ar": "Customer A inv 1832 $900K slipped to W2; promise-to-pay 09-14"
        },
    )
    variance.assert_ties(bridge)
    assert bridge.closing_variance.amount == usd(-940_000).amount

    material = variance.material_rows(bridge)
    assert [row.category for row in material] == ["receipts_trade_ar"]
    assert material[0].explanation is not None
    assert "slipped to W2" in material[0].explanation
    assert material[0].variance.amount == usd(-900_000).amount

    # Everything below threshold is one summary row, with a count, and it is
    # never handed to an agent to explain.
    summary = bridge.rows[-1]
    assert summary.category == variance.IMMATERIAL_LABEL
    assert not summary.material
    assert summary.explanation is not None
    assert "below the 50000.00 USD materiality threshold" in summary.explanation
    assert summary.variance.amount == usd(-40_000).amount


def test_a_week_with_no_immaterial_rows_has_no_summary_row() -> None:
    result = build(open_invoices=(), payroll=None, rent_per_month=None)
    actual = ActualWeek(
        week_start=FIRST_WEEK,
        week_end=FIRST_WEEK + timedelta(days=6),
        opening_cash=usd(20_000_000),
        by_category=dict.fromkeys(forecast.CATEGORIES, usd(100_000)),
    )
    bridge = variance.actual_vs_forecast(result, actual, POLICY, MONTHLY_OPEX)
    assert all(row.material for row in bridge.rows)
    assert len(bridge.rows) == len(forecast.CATEGORIES)
    variance.assert_ties(bridge)


def test_actual_week_helpers() -> None:
    actual = ActualWeek(
        week_start=FIRST_WEEK,
        week_end=FIRST_WEEK + timedelta(days=6),
        opening_cash=usd(1_000_000),
        by_category={"receipts_trade_ar": usd(500_000), "ap_trade": usd(-200_000)},
    )
    assert actual.net_change("USD") == usd(300_000)
    assert actual.closing_cash("USD") == usd(1_300_000)
    assert actual.amount("capex", "USD") == usd(0)


def test_actual_vs_forecast_guards() -> None:
    result = build()
    good = ActualWeek(FIRST_WEEK, FIRST_WEEK + timedelta(days=6), usd(1), {})
    with pytest.raises(PolicyError, match="currency does not match"):
        variance.actual_vs_forecast(
            result,
            ActualWeek(FIRST_WEEK, FIRST_WEEK, Money.of(1, "EUR"), {}),
            POLICY,
            MONTHLY_OPEX,
        )
    with pytest.raises(PolicyError, match="not in the forecast horizon"):
        variance.actual_vs_forecast(
            result,
            ActualWeek(date(2026, 8, 31), date(2026, 9, 6), usd(1), {}),
            POLICY,
            MONTHLY_OPEX,
        )
    with pytest.raises(PolicyError, match="unknown categories"):
        variance.actual_vs_forecast(
            result,
            ActualWeek(FIRST_WEEK, FIRST_WEEK, usd(1), {"made_up": usd(1)}),
            POLICY,
            MONTHLY_OPEX,
        )
    assert variance.actual_vs_forecast(result, good, POLICY, MONTHLY_OPEX) is not None


# --------------------------------------------------------------------------- #
# Forecast vs prior
# --------------------------------------------------------------------------- #


def test_forecast_vs_prior_aligns_by_calendar_week_not_by_index() -> None:
    """This cycle's week 1 is last cycle's week 2."""
    prior = build(FIRST_WEEK)
    current = build(FIRST_WEEK + timedelta(weeks=1), version_id="fv-2")
    bridge = variance.forecast_vs_prior(current, prior, POLICY, MONTHLY_OPEX)
    variance.assert_ties(bridge)
    assert bridge.kind == "forecast_vs_prior"
    # The shared window is the twelve weeks both versions cover, ending on the
    # last day of the prior version's final week.
    assert bridge.week_ending == prior.weeks[-1] + timedelta(days=6)


def test_a_changed_view_of_the_future_shows_up_as_a_material_row() -> None:
    prior = build()
    current = build(
        version_id="fv-2",
        open_invoices=(OpenInvoice("INV-1832", "Customer A", usd(1_200_000), date(2026, 9, 9)),),
    )
    bridge = variance.forecast_vs_prior(
        current,
        prior,
        POLICY,
        MONTHLY_OPEX,
        explanations={"receipts_trade_ar": "Customer A part-paid; open balance revised down"},
    )
    variance.assert_ties(bridge)
    row = next(row for row in bridge.rows if row.category == "receipts_trade_ar")
    assert row.prior_forecast is not None
    assert row.actual is None
    assert row.variance.amount == usd(-3_000_000).amount
    assert row.explanation is not None


def test_forecast_vs_prior_guards() -> None:
    prior = build()
    with pytest.raises(PolicyError, match="share no weeks"):
        variance.forecast_vs_prior(
            build(FIRST_WEEK + timedelta(weeks=20)), prior, POLICY, MONTHLY_OPEX
        )


def test_bridges_in_different_currencies_are_refused() -> None:
    prior = build()
    other = build(
        version_id="fv-eur",
        opening_cash=Money.of(1, "EUR"),
        undrawn_revolver=Money.of(1, "EUR"),
        open_invoices=(),
        payroll=None,
        rent_per_month=None,
        collection_curve=None,
    )
    with pytest.raises(PolicyError, match="different currencies"):
        variance.forecast_vs_prior(other, prior, POLICY, MONTHLY_OPEX)


# --------------------------------------------------------------------------- #
# Ties and rendering
# --------------------------------------------------------------------------- #


def test_assert_ties_catches_a_bridge_that_does_not_add_up() -> None:
    result = build()
    actual = ActualWeek(FIRST_WEEK, FIRST_WEEK + timedelta(days=6), usd(20_000_000), {})
    bridge = variance.actual_vs_forecast(result, actual, POLICY, MONTHLY_OPEX)
    variance.assert_ties(bridge)

    broken = bridge.model_copy(update={"closing_variance": MoneyDTO.from_money(usd(1))})
    with pytest.raises(PolicyError, match="does not tie"):
        variance.assert_ties(broken)


def test_assert_ties_is_a_no_op_for_an_empty_bridge() -> None:
    empty = VarianceBridge(
        kind="forecast_vs_actual",
        week_ending=date(2026, 9, 13),
        rows=(),
        closing_variance=MoneyDTO(amount=17, currency="USD"),
    )
    variance.assert_ties(empty)


def test_render_shows_the_materiality_basis() -> None:
    result = build()
    actual = ActualWeek(
        FIRST_WEEK,
        FIRST_WEEK + timedelta(days=6),
        usd(20_000_000),
        {"receipts_trade_ar": usd(3_100_000)},
    )
    bridge = variance.actual_vs_forecast(result, actual, POLICY, MONTHLY_OPEX)
    rendered = variance.render(bridge, POLICY, MONTHLY_OPEX)
    assert "Week ending 2026-09-13" in rendered
    assert "Actual" in rendered
    assert "Net variance" in rendered
    assert "Materiality: 50000.00 USD" in rendered

    prior_bridge = variance.forecast_vs_prior(
        build(version_id="fv-2"), result, POLICY, MONTHLY_OPEX
    )
    assert "Prior" in variance.render(prior_bridge, POLICY, MONTHLY_OPEX)
