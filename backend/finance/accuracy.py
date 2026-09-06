"""Backtested forecast accuracy — what replaces LLM confidence in this product.

A model reporting `"confidence": 0.87` is unfalsifiable and a finance
professional distrusts it on sight (`WORKFLOW.md` §6). Confidence in treasury is
empirical: measured error, by category, by horizon, over a trailing window. It
buys three things — credibility ("our week-4 customer receipts forecast has been
within 7.8% over 26 weeks" is a claim a CFO can act on), honest stress
calibration ("AR at the 90th percentile of our own observed error" rather than an
arbitrary −10%), and attention routing (categories with near-zero error need no
review).

This requires the seed dataset to carry ~26 weeks of prior `ForecastVersion`
rows. Without that history there is nothing to backtest and the panel is empty —
which is why Phase 2 treats it as non-negotiable rather than as polish.

**The denominator rule.** MAPE is undefined when the actual is zero, and a
weekly cash forecast is full of legitimate zeros — no rent in week 2, no debt
service in week 5. The rule here, applied consistently:

* actual non-zero → denominator is `|actual|` (textbook MAPE);
* actual zero, forecast non-zero → denominator is `|forecast|`, giving 100% error
  for predicting money that never moved, which is the correct verdict;
* both zero → the observation is dropped, because "we correctly predicted that
  nothing would happen" carries no information about percentage error and
  including it as 0% flatters every contractual category.

Errors are basis points of integer arithmetic. A percentage held as a float is a
rounding difference waiting to be argued about in a review meeting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from backend.contracts.forecast import AccuracyStatDTO
from backend.finance.errors import PolicyError
from backend.finance.forecast import CATEGORIES
from backend.finance.money import Money

BPS = 10_000

#: Horizons reported by default: near, month, two months, full horizon.
DEFAULT_HORIZONS: tuple[int, ...] = (1, 4, 8, 13)
DEFAULT_WINDOW_WEEKS = 26

#: Pseudo-category for the bottom row of the accuracy panel. Not a forecast
#: category and never persisted to `accuracy_stats`, whose category column is
#: constrained to the closed set.
NET_CHANGE = "net_change"


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    """What one published version said about one category in one week."""

    #: Week start the version was published for — its own week 1.
    published_for_week: date
    #: Week start being forecast.
    target_week: date
    category: str
    amount: Money

    @property
    def horizon_weeks(self) -> int:
        """1 for the version's own first week, 13 for its last."""
        delta = (self.target_week - self.published_for_week).days
        if delta < 0 or delta % 7:
            raise PolicyError(
                f"target week {self.target_week} is not a whole number of weeks after "
                f"{self.published_for_week}"
            )
        return delta // 7 + 1


@dataclass(frozen=True, slots=True)
class ActualPoint:
    """Settled cash for one category in one closed week."""

    week_start: date
    category: str
    amount: Money


@dataclass(frozen=True, slots=True)
class ErrorSample:
    """One forecast/actual pair and the absolute percentage error it produced."""

    category: str
    horizon_weeks: int
    target_week: date
    forecast: Money
    actual: Money
    ape_bps: int


def _ape_bps(forecast: Money, actual: Money) -> int | None:
    """Absolute percentage error in basis points, or `None` if uninformative."""
    denominator = abs(actual.amount) or abs(forecast.amount)
    if denominator == 0:
        return None
    return abs(actual.amount - forecast.amount) * BPS // denominator


def samples(
    forecasts: tuple[ForecastPoint, ...],
    actuals: tuple[ActualPoint, ...],
    *,
    as_of: date,
    window_weeks: int = DEFAULT_WINDOW_WEEKS,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
) -> tuple[ErrorSample, ...]:
    """Pair forecasts with actuals inside the trailing window."""
    if window_weeks < 1:
        raise PolicyError("window_weeks must be positive")
    if not horizons or any(horizon < 1 for horizon in horizons):
        raise PolicyError("horizons must be positive week counts")

    window_start = as_of - timedelta(weeks=window_weeks)
    actual_by_key = {(point.category, point.week_start): point for point in actuals}
    wanted = set(horizons)

    collected: list[ErrorSample] = []
    for point in forecasts:
        if point.horizon_weeks not in wanted:
            continue
        if not window_start <= point.target_week <= as_of:
            continue
        actual = actual_by_key.get((point.category, point.target_week))
        if actual is None:
            continue
        if actual.amount.currency.code != point.amount.currency.code:
            raise PolicyError(
                f"{point.category} {point.target_week}: forecast and actual currencies differ"
            )
        error = _ape_bps(point.amount, actual.amount)
        if error is None:
            continue
        collected.append(
            ErrorSample(
                category=point.category,
                horizon_weeks=point.horizon_weeks,
                target_week=point.target_week,
                forecast=point.amount,
                actual=actual.amount,
                ape_bps=error,
            )
        )
    return tuple(
        sorted(collected, key=lambda item: (item.category, item.horizon_weeks, item.target_week))
    )


def _mean_bps(values: tuple[int, ...]) -> int:
    """Integer mean, rounded half-up, so the panel is reproducible by hand."""
    total = sum(values)
    count = len(values)
    return (total * 2 + count) // (count * 2)


def compute(
    forecasts: tuple[ForecastPoint, ...],
    actuals: tuple[ActualPoint, ...],
    *,
    as_of: date,
    window_weeks: int = DEFAULT_WINDOW_WEEKS,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    include_net_change: bool = True,
) -> tuple[AccuracyStatDTO, ...]:
    """MAPE by category × horizon — the accuracy panel.

    A category × horizon with no observations is omitted rather than reported as
    zero error. "No data" and "perfect" must not look the same on a screen a CFO
    reads.
    """
    collected = samples(
        forecasts, actuals, as_of=as_of, window_weeks=window_weeks, horizons=horizons
    )

    stats: list[AccuracyStatDTO] = []
    for category in CATEGORIES:
        for horizon in sorted(horizons):
            errors = tuple(
                sample.ape_bps
                for sample in collected
                if sample.category == category and sample.horizon_weeks == horizon
            )
            if not errors:
                continue
            stats.append(
                AccuracyStatDTO(
                    category=category,
                    horizon_weeks=horizon,
                    mape_bps=_mean_bps(errors),
                    n=len(errors),
                    window_weeks=window_weeks,
                )
            )

    if include_net_change:
        stats.extend(
            _net_change_stats(
                forecasts,
                actuals,
                as_of=as_of,
                window_weeks=window_weeks,
                horizons=horizons,
            )
        )
    return tuple(stats)


def _net_change_stats(
    forecasts: tuple[ForecastPoint, ...],
    actuals: tuple[ActualPoint, ...],
    *,
    as_of: date,
    window_weeks: int,
    horizons: tuple[int, ...],
) -> tuple[AccuracyStatDTO, ...]:
    """Error on the weekly net change — the bottom line of the panel.

    Computed on the *summed* week rather than by averaging the category errors:
    offsetting misses inside a week genuinely do cancel in cash terms, and a
    treasurer reading "total net change 2.8%" means the total, not a mean of
    percentages.
    """
    window_start = as_of - timedelta(weeks=window_weeks)
    wanted = set(horizons)

    forecast_totals: dict[tuple[date, int], Money] = {}
    for point in forecasts:
        horizon = point.horizon_weeks
        if horizon not in wanted or not window_start <= point.target_week <= as_of:
            continue
        key = (point.target_week, horizon)
        existing = forecast_totals.get(key)
        forecast_totals[key] = point.amount if existing is None else existing + point.amount

    actual_totals: dict[date, Money] = {}
    for actual in actuals:
        existing = actual_totals.get(actual.week_start)
        actual_totals[actual.week_start] = (
            actual.amount if existing is None else existing + actual.amount
        )

    stats: list[AccuracyStatDTO] = []
    for horizon in sorted(horizons):
        errors: list[int] = []
        for (target_week, point_horizon), forecast_total in sorted(forecast_totals.items()):
            if point_horizon != horizon:
                continue
            actual_total = actual_totals.get(target_week)
            if actual_total is None:
                continue
            error = _ape_bps(forecast_total, actual_total)
            if error is not None:
                errors.append(error)
        if errors:
            stats.append(
                AccuracyStatDTO(
                    category=NET_CHANGE,
                    horizon_weeks=horizon,
                    mape_bps=_mean_bps(tuple(errors)),
                    n=len(errors),
                    window_weeks=window_weeks,
                )
            )
    return tuple(stats)


def percentile_error_bps(
    collected: tuple[ErrorSample, ...],
    category: str,
    horizon_weeks: int,
    percentile: int,
) -> int | None:
    """The `percentile`-th absolute error for a category × horizon.

    This is what makes a stress scenario defensible. "AR at the 90th percentile
    of our own observed 26-week error" is a claim with evidence behind it;
    "AR −10%" is a number someone typed. Returns `None` when there is no history
    to calibrate against — the caller must then say so rather than substitute a
    default, which would reintroduce the arbitrary number by the back door.
    """
    if not 0 <= percentile <= 100:
        raise PolicyError("percentile must be between 0 and 100")
    errors = sorted(
        sample.ape_bps
        for sample in collected
        if sample.category == category and sample.horizon_weeks == horizon_weeks
    )
    if not errors:
        return None
    # Nearest-rank: the smallest value at or above the requested percentile.
    rank = max(1, (percentile * len(errors) + 99) // 100)
    return errors[rank - 1]


def render(
    stats: tuple[AccuracyStatDTO, ...], *, horizons: tuple[int, ...] = DEFAULT_HORIZONS
) -> str:
    """The accuracy panel as `WORKFLOW.md` §6 draws it."""

    def pct(bps: int) -> str:
        whole, frac = divmod(bps, 100)
        return f"{whole}.{frac // 10}%"

    ordered = sorted(horizons)
    header = "Category".ljust(28) + "".join(f"W{h}".rjust(8) for h in ordered) + "n".rjust(6)
    lines = [
        f"Mean absolute percentage error, trailing "
        f"{stats[0].window_weeks if stats else DEFAULT_WINDOW_WEEKS} weeks",
        "",
        header,
        "-" * len(header),
    ]
    categories = [*CATEGORIES, NET_CHANGE]
    for category in categories:
        row_stats = {stat.horizon_weeks: stat for stat in stats if stat.category == category}
        if not row_stats:
            continue
        row = category.ljust(28)
        for horizon in ordered:
            stat = row_stats.get(horizon)
            row += (pct(stat.mape_bps) if stat else "—").rjust(8)
        row += str(max(stat.n for stat in row_stats.values())).rjust(6)
        lines.append(row)
    return "\n".join(lines)


__all__ = [
    "BPS",
    "DEFAULT_HORIZONS",
    "DEFAULT_WINDOW_WEEKS",
    "NET_CHANGE",
    "ActualPoint",
    "ErrorSample",
    "ForecastPoint",
    "compute",
    "percentile_error_bps",
    "render",
    "samples",
]
