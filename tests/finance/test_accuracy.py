"""Accuracy tests — the empirical measure that replaces LLM confidence."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.finance import accuracy
from backend.finance.accuracy import ActualPoint, ForecastPoint
from backend.finance.errors import PolicyError
from backend.finance.money import Money

AS_OF = date(2026, 9, 7)


def usd(major: int) -> Money:
    return Money.of(major * 100, "USD")


def week(offset: int) -> date:
    """Week start `offset` weeks before `AS_OF`."""
    return AS_OF - timedelta(weeks=offset)


def test_horizon_is_derived_from_the_two_week_starts() -> None:
    point = ForecastPoint(
        published_for_week=date(2026, 6, 1),
        target_week=date(2026, 6, 22),
        category="ap_trade",
        amount=usd(1),
    )
    assert point.horizon_weeks == 4


def test_a_horizon_that_is_not_a_whole_number_of_weeks_is_rejected() -> None:
    mid_week = ForecastPoint(date(2026, 6, 1), date(2026, 6, 24), "ap_trade", usd(1))
    backwards = ForecastPoint(date(2026, 6, 8), date(2026, 6, 1), "ap_trade", usd(1))
    with pytest.raises(PolicyError, match="whole number of weeks"):
        assert mid_week.horizon_weeks
    with pytest.raises(PolicyError, match="whole number of weeks"):
        assert backwards.horizon_weeks


def test_mape_is_measured_error_not_a_self_report() -> None:
    """A contractual category has near-zero error; AR does not."""
    forecasts = []
    actuals = []
    for offset in range(1, 5):
        target = week(offset)
        published = target  # horizon 1
        # Rent is contractual: forecast and actual agree exactly.
        forecasts.append(ForecastPoint(published, target, "rent_leases", usd(-300_000)))
        actuals.append(ActualPoint(target, "rent_leases", usd(-300_000)))
        # AR runs 10% short every week.
        forecasts.append(ForecastPoint(published, target, "receipts_trade_ar", usd(1_000_000)))
        actuals.append(ActualPoint(target, "receipts_trade_ar", usd(900_000)))

    stats = accuracy.compute(
        tuple(forecasts), tuple(actuals), as_of=AS_OF, horizons=(1,), include_net_change=False
    )
    by_category = {stat.category: stat for stat in stats}
    assert by_category["rent_leases"].mape_bps == 0
    assert by_category["rent_leases"].n == 4
    # |900k - 1000k| / 900k = 11.11% = 1111 bps.
    assert by_category["receipts_trade_ar"].mape_bps == 1_111
    assert by_category["receipts_trade_ar"].window_weeks == 26


def test_error_widens_with_horizon_which_is_the_whole_point() -> None:
    forecasts = []
    actuals = []
    for offset in range(1, 9):
        target = week(offset)
        actuals.append(ActualPoint(target, "receipts_trade_ar", usd(1_000_000)))
        # Week-1 view is 2% out; week-4 view is 10% out.
        forecasts.append(ForecastPoint(target, target, "receipts_trade_ar", usd(1_020_000)))
        forecasts.append(
            ForecastPoint(target - timedelta(weeks=3), target, "receipts_trade_ar", usd(1_100_000))
        )
    stats = accuracy.compute(
        tuple(forecasts), tuple(actuals), as_of=AS_OF, horizons=(1, 4), include_net_change=False
    )
    by_horizon = {stat.horizon_weeks: stat.mape_bps for stat in stats}
    assert by_horizon[1] == 200
    assert by_horizon[4] == 1_000


def test_the_denominator_rule() -> None:
    target = week(1)
    # Actual zero, forecast non-zero: 100% error for predicting money that never
    # moved — not a skipped observation.
    stats = accuracy.compute(
        (ForecastPoint(target, target, "capex", usd(-500_000)),),
        (ActualPoint(target, "capex", usd(0)),),
        as_of=AS_OF,
        horizons=(1,),
        include_net_change=False,
    )
    assert stats[0].mape_bps == 10_000

    # Both zero: dropped, because "we correctly predicted nothing" is not a
    # percentage and reporting it as 0% flatters every contractual category.
    empty = accuracy.compute(
        (ForecastPoint(target, target, "capex", usd(0)),),
        (ActualPoint(target, "capex", usd(0)),),
        as_of=AS_OF,
        horizons=(1,),
        include_net_change=False,
    )
    assert empty == ()


def test_a_category_with_no_history_is_omitted_not_reported_as_perfect() -> None:
    """ "No data" and "perfect" must not look the same on a CFO's screen."""
    target = week(1)
    stats = accuracy.compute(
        (ForecastPoint(target, target, "ap_trade", usd(-100_000)),),
        (ActualPoint(target, "ap_trade", usd(-100_000)),),
        as_of=AS_OF,
        horizons=(1,),
        include_net_change=False,
    )
    assert {stat.category for stat in stats} == {"ap_trade"}


def test_observations_outside_the_window_or_without_an_actual_are_ignored() -> None:
    stale = week(40)
    future = AS_OF + timedelta(weeks=2)
    recent = week(2)
    stats = accuracy.compute(
        (
            ForecastPoint(stale, stale, "ap_trade", usd(-100_000)),
            ForecastPoint(future, future, "ap_trade", usd(-100_000)),
            ForecastPoint(recent, recent, "ap_trade", usd(-100_000)),
            # An unwanted horizon.
            ForecastPoint(recent - timedelta(weeks=1), recent, "ap_trade", usd(-100_000)),
            # No matching actual.
            ForecastPoint(week(3), week(3), "capex", usd(-1)),
        ),
        (
            ActualPoint(stale, "ap_trade", usd(-90_000)),
            ActualPoint(future, "ap_trade", usd(-90_000)),
            ActualPoint(recent, "ap_trade", usd(-80_000)),
        ),
        as_of=AS_OF,
        horizons=(1,),
        include_net_change=False,
    )
    assert len(stats) == 1
    assert stats[0].n == 1


def test_a_currency_mismatch_is_refused_rather_than_silently_compared() -> None:
    target = week(1)
    with pytest.raises(PolicyError, match="currencies differ"):
        accuracy.compute(
            (ForecastPoint(target, target, "ap_trade", usd(-1)),),
            (ActualPoint(target, "ap_trade", Money.of(-1, "EUR")),),
            as_of=AS_OF,
            horizons=(1,),
        )


def test_sampling_guards() -> None:
    with pytest.raises(PolicyError, match="window_weeks must be positive"):
        accuracy.samples((), (), as_of=AS_OF, window_weeks=0)
    with pytest.raises(PolicyError, match="horizons must be positive"):
        accuracy.samples((), (), as_of=AS_OF, horizons=())
    with pytest.raises(PolicyError, match="horizons must be positive"):
        accuracy.samples((), (), as_of=AS_OF, horizons=(0,))


def test_net_change_is_computed_on_the_summed_week_not_averaged() -> None:
    """Offsetting misses inside a week genuinely do cancel in cash terms."""
    target = week(1)
    forecasts = (
        ForecastPoint(target, target, "receipts_trade_ar", usd(1_000_000)),
        ForecastPoint(target, target, "ap_trade", usd(-1_000_000)),
    )
    actuals = (
        ActualPoint(target, "receipts_trade_ar", usd(1_200_000)),
        ActualPoint(target, "ap_trade", usd(-1_200_000)),
    )
    stats = accuracy.compute(tuple(forecasts), tuple(actuals), as_of=AS_OF, horizons=(1,))
    per_category = {
        stat.category: stat.mape_bps for stat in stats if stat.category != accuracy.NET_CHANGE
    }
    # Each category is 16.67% out...
    assert per_category["receipts_trade_ar"] == 1_666
    assert per_category["ap_trade"] == 1_666
    # ...but both weeks net to zero, so the net-change row is dropped rather
    # than reported as a spurious 16.67%.
    assert accuracy.NET_CHANGE not in {stat.category for stat in stats}


def test_net_change_reports_a_real_weekly_miss() -> None:
    target = week(1)
    stats = accuracy.compute(
        (
            ForecastPoint(target, target, "receipts_trade_ar", usd(1_000_000)),
            ForecastPoint(target, target, "ap_trade", usd(-500_000)),
        ),
        (
            ActualPoint(target, "receipts_trade_ar", usd(900_000)),
            ActualPoint(target, "ap_trade", usd(-500_000)),
        ),
        as_of=AS_OF,
        horizons=(1,),
    )
    net = next(stat for stat in stats if stat.category == accuracy.NET_CHANGE)
    # Forecast net +$500,000 against an actual +$400,000 = 25% error.
    assert net.mape_bps == 2_500
    assert net.n == 1


def test_percentile_error_calibrates_a_stress_scenario() -> None:
    """ "AR at the 90th percentile of our own error" — not an arbitrary −10%."""
    forecasts = []
    actuals = []
    for offset, shortfall in enumerate(
        [10_000, 20_000, 30_000, 40_000, 50_000, 60_000, 70_000, 80_000, 90_000, 500_000],
        start=1,
    ):
        target = week(offset)
        forecasts.append(ForecastPoint(target, target, "receipts_trade_ar", usd(1_000_000)))
        actuals.append(ActualPoint(target, "receipts_trade_ar", usd(1_000_000 - shortfall)))
    collected = accuracy.samples(tuple(forecasts), tuple(actuals), as_of=AS_OF, horizons=(1,))
    assert len(collected) == 10
    p50 = accuracy.percentile_error_bps(collected, "receipts_trade_ar", 1, 50)
    p90 = accuracy.percentile_error_bps(collected, "receipts_trade_ar", 1, 90)
    assert p50 is not None and p90 is not None
    assert p90 > p50
    # The tail observation — a week that collected half of what was forecast —
    # is 100% error against the actual, and it is what a 100th-percentile stress
    # must reach.
    assert accuracy.percentile_error_bps(collected, "receipts_trade_ar", 1, 100) == 10_000


def test_percentile_returns_none_without_history_rather_than_a_default() -> None:
    """A default here would reintroduce the arbitrary number by the back door."""
    assert accuracy.percentile_error_bps((), "receipts_trade_ar", 1, 90) is None
    with pytest.raises(PolicyError, match="percentile must be"):
        accuracy.percentile_error_bps((), "receipts_trade_ar", 1, 101)


def test_render_produces_the_accuracy_panel() -> None:
    forecasts = []
    actuals = []
    for offset in range(1, 27):
        target = week(offset)
        forecasts.append(ForecastPoint(target, target, "rent_leases", usd(-300_000)))
        actuals.append(ActualPoint(target, "rent_leases", usd(-300_000)))
        forecasts.append(ForecastPoint(target, target, "receipts_trade_ar", usd(1_000_000)))
        actuals.append(ActualPoint(target, "receipts_trade_ar", usd(969_000)))
    stats = accuracy.compute(tuple(forecasts), tuple(actuals), as_of=AS_OF, horizons=(1, 4))
    rendered = accuracy.render(stats, horizons=(1, 4))
    assert "Mean absolute percentage error, trailing 26 weeks" in rendered
    assert "rent_leases" in rendered
    assert "0.0%" in rendered
    assert "3.1%" in rendered
    # A horizon with no observations shows as a dash, not as zero error.
    assert "—" in rendered
    # Categories with no history at all are omitted entirely.
    assert "capex" not in rendered


def test_render_of_an_empty_panel_states_the_default_window() -> None:
    assert "trailing 26 weeks" in accuracy.render(())
