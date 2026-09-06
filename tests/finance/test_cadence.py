"""Cadence tests — the dates, not the averages.

`PHASES.md` requires 100% branch coverage here, and the reason is not ceremony:
every branch in this module is a rule about when money moves, and an untested
branch is a rule nobody has checked. The assertions below are therefore stated
against known 2026 calendar facts rather than against the code's own output.
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.finance import cadence
from backend.finance.cadence import (
    BusinessCalendar,
    CadenceError,
    Convention,
    DepositSchedule,
    PayrollFrequency,
    ServiceFrequency,
)
from backend.finance.errors import MoneyError
from backend.finance.money import Money

CAL = cadence.us_calendar(2025, 2027)


def usd(amount: int) -> Money:
    return Money.of(amount, "USD")


# --------------------------------------------------------------------------- #
# Holidays and business days
# --------------------------------------------------------------------------- #


def test_2026_federal_holidays_are_the_real_ones() -> None:
    holidays = {day for day in CAL.holidays if day.year == 2026}
    assert holidays == {
        date(2026, 1, 1),  # New Year's Day, Thursday
        date(2026, 1, 19),  # MLK, 3rd Monday
        date(2026, 2, 16),  # Washington's Birthday, 3rd Monday
        date(2026, 5, 25),  # Memorial Day, last Monday
        date(2026, 6, 19),  # Juneteenth, Friday
        date(2026, 9, 7),  # Labor Day, 1st Monday
        date(2026, 10, 12),  # Columbus Day, 2nd Monday
        date(2026, 11, 11),  # Veterans Day, Wednesday
        date(2026, 11, 26),  # Thanksgiving, 4th Thursday
        date(2026, 12, 25),  # Christmas, Friday
    }


def test_sunday_holiday_is_observed_monday_and_saturday_is_not_observed() -> None:
    # 2027-07-04 is a Sunday: the Fed observes Monday the 5th.
    holidays = cadence.us_federal_holidays(2027, 2027)
    assert date(2027, 7, 5) in holidays
    assert date(2027, 7, 4) not in holidays
    # 2026-07-04 is a Saturday: the Fed does not observe it at all, so the wire
    # runs normally on Friday the 3rd.
    assert date(2026, 7, 3) not in CAL.holidays
    assert date(2026, 7, 4) not in CAL.holidays


def test_holiday_year_range_must_be_ordered() -> None:
    with pytest.raises(CadenceError):
        cadence.us_federal_holidays(2027, 2026)


def test_business_day_predicates() -> None:
    assert CAL.is_business_day(date(2026, 9, 3))  # Thursday
    assert not CAL.is_business_day(date(2026, 9, 5))  # Saturday
    assert not CAL.is_business_day(date(2026, 9, 7))  # Labor Day
    assert CAL.next_business_day(date(2026, 9, 4)) == date(2026, 9, 8)
    assert CAL.previous_business_day(date(2026, 9, 8)) == date(2026, 9, 4)


@pytest.mark.parametrize(
    ("day", "convention", "expected"),
    [
        # A business day is never moved, whatever the convention.
        (date(2026, 9, 3), Convention.FOLLOWING, date(2026, 9, 3)),
        (date(2026, 9, 5), Convention.NONE, date(2026, 9, 5)),
        (date(2026, 9, 7), Convention.FOLLOWING, date(2026, 9, 8)),
        (date(2026, 9, 7), Convention.PRECEDING, date(2026, 9, 4)),
        # Modified following rolls forward inside the month...
        (date(2026, 9, 5), Convention.MODIFIED_FOLLOWING, date(2026, 9, 8)),
        # ...but never across it: 2026-10-31 is a Saturday, so it steps back.
        (date(2026, 10, 31), Convention.MODIFIED_FOLLOWING, date(2026, 10, 30)),
    ],
)
def test_adjust_conventions(day: date, convention: Convention, expected: date) -> None:
    assert CAL.adjust(day, convention) == expected


def test_add_business_days() -> None:
    assert CAL.add_business_days(date(2026, 9, 3), 0) == date(2026, 9, 3)
    # Zero business days from a closed day snaps forward.
    assert CAL.add_business_days(date(2026, 9, 5), 0) == date(2026, 9, 8)
    assert CAL.add_business_days(date(2026, 9, 4), 2) == date(2026, 9, 9)
    with pytest.raises(CadenceError):
        CAL.add_business_days(date(2026, 9, 4), -1)


def test_business_days_between_handles_an_inverted_range() -> None:
    assert CAL.business_days_between(date(2026, 9, 10), date(2026, 9, 1)) == ()
    assert CAL.business_days_between(date(2026, 9, 7), date(2026, 9, 11)) == (
        date(2026, 9, 8),
        date(2026, 9, 9),
        date(2026, 9, 10),
        date(2026, 9, 11),
    )


# --------------------------------------------------------------------------- #
# Week bucketing
# --------------------------------------------------------------------------- #


def test_week_helpers() -> None:
    wednesday = date(2026, 9, 9)
    assert cadence.week_start(wednesday) == date(2026, 9, 7)
    assert cadence.week_end(wednesday) == date(2026, 9, 13)
    assert cadence.week_starts(date(2026, 9, 7), 3) == (
        date(2026, 9, 7),
        date(2026, 9, 14),
        date(2026, 9, 21),
    )


def test_week_index_is_one_based_and_zero_before_the_grid() -> None:
    first = date(2026, 9, 7)
    assert cadence.week_index(date(2026, 9, 7), first) == 1
    assert cadence.week_index(date(2026, 9, 13), first) == 1
    assert cadence.week_index(date(2026, 9, 14), first) == 2
    # Activity before the grid is dropped rather than folded into week 1.
    assert cadence.week_index(date(2026, 9, 1), first) == 0


def test_week_helpers_reject_a_non_monday_or_empty_count() -> None:
    with pytest.raises(CadenceError):
        cadence.week_starts(date(2026, 9, 8), 3)
    with pytest.raises(CadenceError):
        cadence.week_starts(date(2026, 9, 7), 0)
    with pytest.raises(CadenceError):
        cadence.week_index(date(2026, 9, 9), date(2026, 9, 8))


# --------------------------------------------------------------------------- #
# Month arithmetic
# --------------------------------------------------------------------------- #


def test_add_months_clamps_to_a_shorter_month() -> None:
    assert cadence.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert cadence.add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)
    assert cadence.add_months(date(2026, 1, 15), -1) == date(2025, 12, 15)


def test_month_starts() -> None:
    assert cadence.month_starts(date(2026, 9, 15), date(2026, 11, 3)) == (
        date(2026, 9, 1),
        date(2026, 10, 1),
        date(2026, 11, 1),
    )
    assert cadence.month_starts(date(2026, 11, 1), date(2026, 9, 1)) == ()


# --------------------------------------------------------------------------- #
# Payroll
# --------------------------------------------------------------------------- #


def test_semi_monthly_payroll_is_paid_early_when_the_date_is_closed() -> None:
    dates = cadence.semi_monthly_pay_dates(date(2026, 9, 1), date(2026, 12, 31), CAL)
    assert dates == (
        date(2026, 9, 15),
        date(2026, 9, 30),
        date(2026, 10, 15),
        date(2026, 10, 30),  # the 31st is a Saturday
        date(2026, 11, 13),  # the 15th is a Sunday, the 14th a Saturday
        date(2026, 11, 30),
        date(2026, 12, 15),
        date(2026, 12, 31),
    )


def test_semi_monthly_rejects_an_impossible_mid_month_day() -> None:
    with pytest.raises(CadenceError):
        cadence.semi_monthly_pay_dates(date(2026, 9, 1), date(2026, 9, 30), CAL, mid_month_day=31)


def test_biweekly_payroll_produces_three_payrolls_in_two_months_a_year() -> None:
    """The single most common cash surprise (`WORKFLOW.md` §3)."""
    dates = cadence.biweekly_pay_dates(date(2026, 1, 2), date(2026, 1, 1), date(2026, 12, 31), CAL)
    assert len(dates) == 26
    assert cadence.three_payroll_months(dates) == ((2026, 1), (2026, 7))


def test_biweekly_accepts_an_anchor_after_the_window_start() -> None:
    dates = cadence.biweekly_pay_dates(date(2026, 6, 5), date(2026, 1, 5), date(2026, 2, 28), CAL)
    assert dates[0] == date(2026, 1, 16)
    assert all(date(2026, 1, 5) <= day <= date(2026, 2, 28) for day in dates)


def test_biweekly_returns_nothing_for_an_inverted_window() -> None:
    assert (
        cadence.biweekly_pay_dates(date(2026, 1, 2), date(2026, 3, 1), date(2026, 1, 1), CAL) == ()
    )


def test_pay_dates_dispatches_on_frequency() -> None:
    semi = cadence.pay_dates(
        PayrollFrequency.SEMI_MONTHLY, date(2026, 9, 1), date(2026, 9, 30), CAL
    )
    assert semi == (date(2026, 9, 15), date(2026, 9, 30))
    biweekly = cadence.pay_dates(
        PayrollFrequency.BIWEEKLY,
        date(2026, 9, 1),
        date(2026, 9, 30),
        CAL,
        anchor=date(2026, 1, 2),
    )
    assert biweekly == (date(2026, 9, 11), date(2026, 9, 25))
    with pytest.raises(CadenceError):
        cadence.pay_dates(PayrollFrequency.BIWEEKLY, date(2026, 9, 1), date(2026, 9, 30), CAL)


def test_payroll_tax_deposits_follow_their_own_schedule() -> None:
    """Payroll taxes are not payroll: separate line, separate date."""
    # Tuesday payday deposits the following Friday; Wednesday payday the
    # following Wednesday.
    assert cadence.payroll_tax_deposit_dates((date(2026, 9, 15),), CAL) == (date(2026, 9, 18),)
    assert cadence.payroll_tax_deposit_dates((date(2026, 9, 30),), CAL) == (date(2026, 10, 7),)
    # A Friday payday deposits the *next* Wednesday, never the same day.
    assert cadence.payroll_tax_deposit_dates((date(2026, 9, 4),), CAL) == (date(2026, 9, 9),)


def test_monthly_deposit_schedule_is_the_15th_of_the_following_month() -> None:
    assert cadence.payroll_tax_deposit_dates(
        (date(2026, 9, 15), date(2026, 9, 30)), CAL, schedule=DepositSchedule.MONTHLY
    ) == (date(2026, 10, 15),)


def test_deposit_due_on_a_closed_day_rolls_forward() -> None:
    # 2026-11-11 is Veterans Day, a Wednesday: the deposit moves to the 12th.
    assert cadence.payroll_tax_deposit_dates((date(2026, 11, 6),), CAL) == (date(2026, 11, 12),)


# --------------------------------------------------------------------------- #
# AP, rent, tax, debt service
# --------------------------------------------------------------------------- #


def test_ap_runs_are_thursday_batch_events() -> None:
    runs = cadence.ap_run_dates(date(2026, 9, 1), date(2026, 9, 30), CAL)
    assert runs == (date(2026, 9, 3), date(2026, 9, 10), date(2026, 9, 17), date(2026, 9, 24))
    assert all(run.weekday() == cadence.THURSDAY for run in runs)


def test_ap_run_on_a_holiday_moves_earlier() -> None:
    # Thanksgiving 2026-11-26 is a Thursday: that week's run is the 25th.
    runs = cadence.ap_run_dates(date(2026, 11, 23), date(2026, 11, 29), CAL)
    assert runs == (date(2026, 11, 25),)


def test_ap_run_guards() -> None:
    assert cadence.ap_run_dates(date(2026, 9, 30), date(2026, 9, 1), CAL) == ()
    with pytest.raises(CadenceError):
        cadence.ap_run_dates(date(2026, 9, 1), date(2026, 9, 30), CAL, weekday=7)


def test_rent_is_due_on_the_first_and_rolls_forward() -> None:
    assert cadence.monthly_dates(date(2026, 8, 1), date(2026, 12, 31), CAL) == (
        date(2026, 8, 3),  # the 1st is a Saturday
        date(2026, 9, 1),
        date(2026, 10, 1),
        date(2026, 11, 2),  # the 1st is a Sunday
        date(2026, 12, 1),
    )


def test_monthly_dates_validate_the_day() -> None:
    with pytest.raises(CadenceError):
        cadence.monthly_dates(date(2026, 9, 1), date(2026, 9, 30), CAL, day_of_month=32)


def test_short_month_clamps_within_the_window() -> None:
    # February 2026 has 28 days; a 31st-of-month obligation clamps to the 28th,
    # a Saturday, which rolls forward to Monday 2026-03-02 — outside the window,
    # so nothing is emitted rather than a date being invented.
    assert cadence.monthly_dates(date(2026, 2, 1), date(2026, 2, 28), CAL, day_of_month=31) == ()


def test_quarterly_estimated_tax_dates() -> None:
    assert cadence.quarterly_estimated_tax_dates(date(2026, 1, 1), date(2026, 12, 31), CAL) == (
        date(2026, 4, 15),
        date(2026, 6, 15),
        date(2026, 9, 15),
        date(2026, 12, 15),
    )
    assert cadence.quarterly_estimated_tax_dates(date(2026, 12, 31), date(2026, 1, 1), CAL) == ()


def test_debt_service_dates_walk_back_to_the_anchor() -> None:
    """A month-end schedule stays on month-end instead of drifting a day early."""
    dates = cadence.debt_service_dates(date(2026, 3, 31), date(2026, 9, 1), date(2027, 3, 31), CAL)
    assert dates == (date(2026, 9, 30), date(2026, 12, 31), date(2027, 3, 31))


def test_add_months_eom_preserves_month_end() -> None:
    assert cadence.add_months_eom(date(2026, 3, 31), 3) == date(2026, 6, 30)
    assert cadence.add_months_eom(date(2026, 6, 30), 3) == date(2026, 9, 30)
    # A mid-month anchor is not treated as month-end.
    assert cadence.add_months_eom(date(2026, 3, 15), 3) == date(2026, 6, 15)
    assert (
        cadence.debt_service_dates(date(2026, 3, 31), date(2027, 1, 1), date(2026, 1, 1), CAL) == ()
    )


@pytest.mark.parametrize(
    ("frequency", "count"),
    [
        (ServiceFrequency.MONTHLY, 12),
        (ServiceFrequency.QUARTERLY, 4),
        (ServiceFrequency.SEMIANNUAL, 2),
        (ServiceFrequency.ANNUAL, 1),
    ],
)
def test_debt_service_frequencies(frequency: ServiceFrequency, count: int) -> None:
    dates = cadence.debt_service_dates(
        date(2026, 1, 15), date(2026, 1, 1), date(2026, 12, 31), CAL, frequency=frequency
    )
    assert len(dates) == count


# --------------------------------------------------------------------------- #
# Weekday weighting and settlement
# --------------------------------------------------------------------------- #


def test_receipts_are_weekday_weighted_and_never_land_on_a_weekend() -> None:
    parts = cadence.distribute_over_week(usd(1_000_000), date(2026, 9, 14), CAL)
    assert [day.weekday() for day, _ in parts] == [0, 1, 2, 3, 4]
    # The split is exact: no minor unit is created or lost by the weighting.
    assert Money.sum((amount for _, amount in parts), "USD") == usd(1_000_000)
    # Front-loaded, per the default weights.
    assert parts[0][1] > parts[-1][1]


def test_distribution_skips_a_bank_holiday() -> None:
    # Labor Day week: Monday is closed, so Monday receives nothing.
    parts = cadence.distribute_over_week(usd(1_000_000), date(2026, 9, 7), CAL)
    assert [day for day, _ in parts] == [
        date(2026, 9, 8),
        date(2026, 9, 9),
        date(2026, 9, 10),
        date(2026, 9, 11),
    ]
    assert Money.sum((amount for _, amount in parts), "USD") == usd(1_000_000)


def test_distribution_guards() -> None:
    with pytest.raises(CadenceError):
        cadence.distribute_over_week(usd(100), date(2026, 9, 8), CAL)
    # A week entirely closed has nothing to distribute across.
    shut = BusinessCalendar(weekend=frozenset(range(7)))
    with pytest.raises(CadenceError):
        cadence.distribute_over_week(usd(100), date(2026, 9, 7), shut)


def test_weekday_weight_guards() -> None:
    with pytest.raises(CadenceError):
        cadence.weekday_weights((date(2026, 9, 7),), (-1, 1, 1, 1, 1))
    with pytest.raises(CadenceError):
        # Saturday only, and weekends carry zero weight.
        cadence.weekday_weights((date(2026, 9, 12),))


def test_settlement_shifts_across_a_monday_holiday() -> None:
    """A payment dated Friday before a Monday holiday settles Tuesday."""
    assert cadence.settlement_date(date(2026, 9, 4), CAL) == date(2026, 9, 8)


def test_allocate_by_weight_requires_weights() -> None:
    assert cadence.allocate_by_weight(usd(100), [1, 1]) == (usd(50), usd(50))
    with pytest.raises(MoneyError):
        cadence.allocate_by_weight(usd(100), [])


# --------------------------------------------------------------------------- #
# Window edges — a generated date outside the requested window is dropped,
# never clamped into it. Clamping is how a forecast acquires a payroll that
# does not exist.
# --------------------------------------------------------------------------- #


def test_pay_date_before_the_window_is_dropped_not_clamped() -> None:
    dates = cadence.semi_monthly_pay_dates(date(2026, 9, 20), date(2026, 10, 10), CAL)
    assert dates == (date(2026, 9, 30),)


def test_ap_run_pulled_earlier_than_the_window_start_is_dropped() -> None:
    # The window opens on Thanksgiving; that week's run was pulled to the 25th,
    # which is before the window, so it belongs to the previous cycle.
    assert cadence.ap_run_dates(date(2026, 11, 26), date(2026, 11, 30), CAL) == ()


def test_quarterly_tax_dates_outside_the_window_are_dropped() -> None:
    assert cadence.quarterly_estimated_tax_dates(date(2026, 5, 1), date(2026, 10, 1), CAL) == (
        date(2026, 6, 15),
        date(2026, 9, 15),
    )
