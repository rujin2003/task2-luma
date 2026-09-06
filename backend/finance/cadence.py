"""The calendar layer: when money actually moves.

`WORKFLOW.md` §3 is blunt about why this module exists. A forecast that spreads
payroll as "monthly / 4.33" is wrong twice a year by a whole payroll, a forecast
that treats AP as a smooth outflow shows a flat line where the bank shows a
Thursday spike, and a forecast that spreads collections across seven days is
wrong in week 1 — the week the treasurer actually reads.

So every cadence here generates **dates**, never averages:

* semi-monthly and biweekly pay dates, with the three-payroll months falling out
  of the arithmetic rather than being special-cased;
* payroll-tax deposits on the IRS semi-weekly or monthly lookback schedule —
  a separate line on a separate date, because they are not payroll;
* AP runs on a fixed weekday, quarterly estimated tax, 1st-of-month rent,
  debt service on facility anniversaries;
* weekday weighting for receipts;
* bank holidays and the settlement shift they cause.

Business-day adjustment is explicit at every call site via `Convention`, because
the correct direction differs by obligation: payroll is paid *early* when the pay
date is closed (PRECEDING), tax deposits are due the *next* business day
(FOLLOWING), and loan schedules use MODIFIED_FOLLOWING so a payment never slips
into the next month.

No floats: weekday weights are basis points and every split goes through
`Money.allocate`, so distributed amounts sum back to the original exactly.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum

from backend.finance.errors import MoneyError
from backend.finance.money import Money

MONDAY = 0
TUESDAY = 1
WEDNESDAY = 2
THURSDAY = 3
FRIDAY = 4
SATURDAY = 5
SUNDAY = 6

DEFAULT_WEEKEND: frozenset[int] = frozenset({SATURDAY, SUNDAY})

#: Receipts arrive on business days, front-loaded in the week: lockbox and ACH
#: batches posted overnight Monday clear against the largest backlog. Basis
#: points over Mon-Fri, summing to 10_000.
DEFAULT_RECEIPT_WEIGHTS_BPS: tuple[int, ...] = (2_200, 2_100, 2_000, 1_900, 1_800)


class Convention(StrEnum):
    """How to adjust a date that lands on a non-business day."""

    NONE = "none"
    FOLLOWING = "following"
    PRECEDING = "preceding"
    MODIFIED_FOLLOWING = "modified_following"


class PayrollFrequency(StrEnum):
    SEMI_MONTHLY = "semi_monthly"
    BIWEEKLY = "biweekly"


class DepositSchedule(StrEnum):
    """IRS employment-tax deposit schedule, set by a lookback period."""

    SEMI_WEEKLY = "semi_weekly"
    MONTHLY = "monthly"


class ServiceFrequency(StrEnum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMIANNUAL = "semiannual"
    ANNUAL = "annual"


_SERVICE_MONTHS: dict[ServiceFrequency, int] = {
    ServiceFrequency.MONTHLY: 1,
    ServiceFrequency.QUARTERLY: 3,
    ServiceFrequency.SEMIANNUAL: 6,
    ServiceFrequency.ANNUAL: 12,
}


class CadenceError(ValueError):
    """A cadence was asked for something it cannot generate."""


# --------------------------------------------------------------------------- #
# Calendar
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class BusinessCalendar:
    """Weekend rule plus a holiday set, in one place.

    Holidays are supplied rather than computed on demand so a tenant in another
    jurisdiction can hand us their own set without touching this module.
    """

    holidays: frozenset[date] = field(default_factory=frozenset)
    weekend: frozenset[int] = DEFAULT_WEEKEND

    def is_business_day(self, day: date) -> bool:
        return day.weekday() not in self.weekend and day not in self.holidays

    def next_business_day(self, day: date) -> date:
        """The first business day strictly after `day`."""
        cursor = day + timedelta(days=1)
        while not self.is_business_day(cursor):
            cursor += timedelta(days=1)
        return cursor

    def previous_business_day(self, day: date) -> date:
        """The last business day strictly before `day`."""
        cursor = day - timedelta(days=1)
        while not self.is_business_day(cursor):
            cursor -= timedelta(days=1)
        return cursor

    def adjust(self, day: date, convention: Convention) -> date:
        """Move `day` onto a business day per `convention`."""
        if convention is Convention.NONE or self.is_business_day(day):
            return day
        if convention is Convention.FOLLOWING:
            return self.next_business_day(day)
        if convention is Convention.PRECEDING:
            return self.previous_business_day(day)
        # MODIFIED_FOLLOWING: roll forward, but never into the next month.
        forward = self.next_business_day(day)
        if forward.month == day.month:
            return forward
        return self.previous_business_day(day)

    def add_business_days(self, day: date, count: int) -> date:
        """Settlement arithmetic: `count` business days from `day`.

        `count == 0` snaps a non-business day forward, which is what "settles
        same day" means when the day itself is closed.
        """
        if count < 0:
            raise CadenceError("add_business_days requires a non-negative count")
        cursor = day
        if count == 0:
            return cursor if self.is_business_day(cursor) else self.next_business_day(cursor)
        for _ in range(count):
            cursor = self.next_business_day(cursor)
        return cursor

    def business_days_between(self, start: date, end: date) -> tuple[date, ...]:
        """Business days in the inclusive range `[start, end]`."""
        if end < start:
            return ()
        return tuple(day for day in _date_range(start, end) if self.is_business_day(day))


def _date_range(start: date, end: date) -> Iterator[date]:
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


# --------------------------------------------------------------------------- #
# US federal bank holidays
# --------------------------------------------------------------------------- #


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    """The `nth` (1-based) `weekday` of a month."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (nth - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The final `weekday` of a month."""
    last_day = _month_end(date(year, month, 1))
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def _fed_observed(day: date) -> date | None:
    """Federal Reserve observance rule for a fixed-date holiday.

    Sunday holidays are observed the following Monday. Saturday holidays are
    **not** observed — the Fed wire runs normally on the Friday before. Treating
    them as observed shifts a settlement date by a day, which is precisely the
    class of error this module exists to avoid.
    """
    if day.weekday() == SUNDAY:
        return day + timedelta(days=1)
    if day.weekday() == SATURDAY:
        return None
    return day


def us_federal_holidays(start_year: int, end_year: int) -> frozenset[date]:
    """Observed US federal bank holidays across an inclusive year range."""
    if end_year < start_year:
        raise CadenceError("end_year must be >= start_year")
    observed: set[date] = set()
    for year in range(start_year, end_year + 1):
        floating = (
            _nth_weekday(year, 1, MONDAY, 3),  # Martin Luther King Jr. Day
            _nth_weekday(year, 2, MONDAY, 3),  # Washington's Birthday
            _last_weekday(year, 5, MONDAY),  # Memorial Day
            _nth_weekday(year, 9, MONDAY, 1),  # Labor Day
            _nth_weekday(year, 10, MONDAY, 2),  # Columbus Day
            _nth_weekday(year, 11, THURSDAY, 4),  # Thanksgiving
        )
        observed.update(floating)
        fixed = (
            date(year, 1, 1),  # New Year's Day
            date(year, 6, 19),  # Juneteenth
            date(year, 7, 4),  # Independence Day
            date(year, 11, 11),  # Veterans Day
            date(year, 12, 25),  # Christmas Day
        )
        for day in fixed:
            shifted = _fed_observed(day)
            if shifted is not None:
                observed.add(shifted)
    return frozenset(observed)


def us_calendar(start_year: int, end_year: int) -> BusinessCalendar:
    return BusinessCalendar(holidays=us_federal_holidays(start_year, end_year))


# --------------------------------------------------------------------------- #
# Week bucketing — ISO weeks, Monday start, everywhere in this system
# --------------------------------------------------------------------------- #


def week_start(day: date) -> date:
    """The Monday of `day`'s ISO week."""
    return day - timedelta(days=day.weekday())


def week_end(day: date) -> date:
    """The Sunday of `day`'s ISO week."""
    return week_start(day) + timedelta(days=6)


def week_starts(first_week_start: date, count: int) -> tuple[date, ...]:
    """`count` consecutive week-start Mondays beginning at `first_week_start`."""
    if count < 1:
        raise CadenceError("week_starts requires a positive count")
    if first_week_start.weekday() != MONDAY:
        raise CadenceError(f"{first_week_start} is not a Monday")
    return tuple(first_week_start + timedelta(weeks=index) for index in range(count))


def week_index(day: date, first_week_start: date) -> int:
    """1-based week bucket of `day` relative to `first_week_start`.

    Returns 0 when `day` falls before the grid, which callers use to drop
    already-closed activity rather than silently bucketing it into week 1.
    """
    if first_week_start.weekday() != MONDAY:
        raise CadenceError(f"{first_week_start} is not a Monday")
    delta_days = (week_start(day) - first_week_start).days
    if delta_days < 0:
        return 0
    return delta_days // 7 + 1


# --------------------------------------------------------------------------- #
# Month arithmetic
# --------------------------------------------------------------------------- #


def _month_end(day: date) -> date:
    if day.month == 12:
        return date(day.year, 12, 31)
    return date(day.year, day.month + 1, 1) - timedelta(days=1)


def add_months(day: date, months: int) -> date:
    """Shift by whole months, clamping to the end of a shorter month."""
    total = day.year * 12 + (day.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    last = _month_end(date(year, month, 1)).day
    return date(year, month, min(day.day, last))


def add_months_eom(day: date, months: int) -> date:
    """Shift by whole months, preserving an end-of-month anchor.

    Loan documents use the end-of-month convention: a facility whose service
    date is 31 March pays on 30 June and 30 September, not on the 30th of every
    month thereafter. Plain `add_months` clamps 31 March to 30 June and then
    treats *that* as the anchor, so the schedule silently walks off month-end
    and every later date is a day early.
    """
    if day == _month_end(day):
        shifted = add_months(date(day.year, day.month, 1), months)
        return _month_end(shifted)
    return add_months(day, months)


def month_starts(start: date, end: date) -> tuple[date, ...]:
    """First-of-month dates from the month containing `start` through `end`."""
    if end < start:
        return ()
    cursor = date(start.year, start.month, 1)
    months: list[date] = []
    while cursor <= end:
        months.append(cursor)
        cursor = add_months(cursor, 1)
    return tuple(months)


# --------------------------------------------------------------------------- #
# Payroll
# --------------------------------------------------------------------------- #


def semi_monthly_pay_dates(
    start: date,
    end: date,
    calendar: BusinessCalendar,
    *,
    mid_month_day: int = 15,
) -> tuple[date, ...]:
    """Pay on the 15th and the last day of each month, paid early if closed.

    Payroll is the one obligation that moves *backwards*: an employer whose pay
    date lands on a holiday funds it the business day before, never after.
    """
    if not 1 <= mid_month_day <= 28:
        raise CadenceError("mid_month_day must be between 1 and 28")
    dates: list[date] = []
    for first in month_starts(start, end):
        for raw in (date(first.year, first.month, mid_month_day), _month_end(first)):
            paid = calendar.adjust(raw, Convention.PRECEDING)
            if start <= paid <= end:
                dates.append(paid)
    return tuple(sorted(set(dates)))


def biweekly_pay_dates(
    anchor: date,
    start: date,
    end: date,
    calendar: BusinessCalendar,
) -> tuple[date, ...]:
    """Every 14 days from `anchor`, paid early if the date is closed.

    The two three-payroll months a year are not special-cased here; they emerge
    because 26 pay dates do not divide evenly into 12 months. `three_payroll_
    months` reports them for the forecast narrative.
    """
    if end < start:
        return ()
    # Walk back to the last anchor occurrence at or before `start`, so a caller
    # can pass a historical anchor without generating thousands of dates.
    cursor = anchor
    if cursor > start:
        periods = (cursor - start).days // 14 + 1
        cursor -= timedelta(days=14 * periods)
    dates: list[date] = []
    while cursor <= end:
        if cursor >= start - timedelta(days=13):
            paid = calendar.adjust(cursor, Convention.PRECEDING)
            if start <= paid <= end:
                dates.append(paid)
        cursor += timedelta(days=14)
    return tuple(sorted(set(dates)))


def three_payroll_months(pay_dates: Iterable[date]) -> tuple[tuple[int, int], ...]:
    """`(year, month)` pairs receiving three payrolls — the cash surprise."""
    counts: dict[tuple[int, int], int] = {}
    for day in pay_dates:
        key = (day.year, day.month)
        counts[key] = counts.get(key, 0) + 1
    return tuple(sorted(key for key, count in counts.items() if count >= 3))


def pay_dates(
    frequency: PayrollFrequency,
    start: date,
    end: date,
    calendar: BusinessCalendar,
    *,
    anchor: date | None = None,
    mid_month_day: int = 15,
) -> tuple[date, ...]:
    """Dispatch to the configured payroll frequency."""
    if frequency is PayrollFrequency.SEMI_MONTHLY:
        return semi_monthly_pay_dates(start, end, calendar, mid_month_day=mid_month_day)
    if anchor is None:
        raise CadenceError("biweekly payroll requires an anchor pay date")
    return biweekly_pay_dates(anchor, start, end, calendar)


def payroll_tax_deposit_dates(
    pay_days: Sequence[date],
    calendar: BusinessCalendar,
    *,
    schedule: DepositSchedule = DepositSchedule.SEMI_WEEKLY,
) -> tuple[date, ...]:
    """Deposit dates for the employment taxes withheld on `pay_days`.

    Separate line, separate date (`WORKFLOW.md` §3). Under the semi-weekly rule a
    Wednesday/Thursday/Friday payday deposits the following Wednesday and a
    Saturday-through-Tuesday payday deposits the following Friday; under the
    monthly rule everything withheld in a month deposits on the 15th of the next.
    Both roll forward to the next business day when the due date is closed.
    """
    deposits: set[date] = set()
    for payday in pay_days:
        if schedule is DepositSchedule.MONTHLY:
            due = add_months(date(payday.year, payday.month, 15), 1)
        else:
            target = WEDNESDAY if payday.weekday() in (WEDNESDAY, THURSDAY, FRIDAY) else FRIDAY
            offset = (target - payday.weekday()) % 7
            due = payday + timedelta(days=offset or 7)
        deposits.add(calendar.adjust(due, Convention.FOLLOWING))
    return tuple(sorted(deposits))


# --------------------------------------------------------------------------- #
# AP, rent, tax, debt service
# --------------------------------------------------------------------------- #


def ap_run_dates(
    start: date,
    end: date,
    calendar: BusinessCalendar,
    *,
    weekday: int = THURSDAY,
) -> tuple[date, ...]:
    """Weekly batch AP runs on a fixed weekday, pulled earlier when closed.

    The grid should show a spike on these dates and nothing between them.
    """
    if not 0 <= weekday <= 6:
        raise CadenceError("weekday must be 0 (Monday) through 6 (Sunday)")
    if end < start:
        return ()
    cursor = start + timedelta(days=(weekday - start.weekday()) % 7)
    runs: list[date] = []
    while cursor <= end:
        adjusted = calendar.adjust(cursor, Convention.PRECEDING)
        if start <= adjusted <= end:
            runs.append(adjusted)
        cursor += timedelta(days=7)
    return tuple(sorted(set(runs)))


def monthly_dates(
    start: date,
    end: date,
    calendar: BusinessCalendar,
    *,
    day_of_month: int = 1,
    convention: Convention = Convention.FOLLOWING,
) -> tuple[date, ...]:
    """Rent and other 1st-of-month obligations."""
    if not 1 <= day_of_month <= 31:
        raise CadenceError("day_of_month must be between 1 and 31")
    dates: list[date] = []
    for first in month_starts(start, end):
        last = _month_end(first).day
        raw = date(first.year, first.month, min(day_of_month, last))
        adjusted = calendar.adjust(raw, convention)
        if start <= adjusted <= end:
            dates.append(adjusted)
    return tuple(sorted(set(dates)))


def quarterly_estimated_tax_dates(
    start: date,
    end: date,
    calendar: BusinessCalendar,
) -> tuple[date, ...]:
    """15 Apr / 15 Jun / 15 Sep / 15 Dec, rolled forward when closed."""
    if end < start:
        return ()
    dates: list[date] = []
    for year in range(start.year, end.year + 1):
        for month in (4, 6, 9, 12):
            due = calendar.adjust(date(year, month, 15), Convention.FOLLOWING)
            if start <= due <= end:
                dates.append(due)
    return tuple(sorted(set(dates)))


def debt_service_dates(
    anchor: date,
    start: date,
    end: date,
    calendar: BusinessCalendar,
    *,
    frequency: ServiceFrequency = ServiceFrequency.QUARTERLY,
) -> tuple[date, ...]:
    """Facility anniversary dates, MODIFIED_FOLLOWING as loan documents specify."""
    if end < start:
        return ()
    step = _SERVICE_MONTHS[frequency]
    # Rewind to the last anniversary at or before the window, then walk forward.
    # Offsets are always measured from `anchor` rather than from the previous
    # cursor, so a month-end schedule cannot drift a day earlier each period.
    periods = 0
    while add_months_eom(anchor, periods * step) > start:
        periods -= 1
    dates: list[date] = []
    while True:
        raw = add_months_eom(anchor, periods * step)
        if raw > end:
            break
        adjusted = calendar.adjust(raw, Convention.MODIFIED_FOLLOWING)
        if start <= adjusted <= end:
            dates.append(adjusted)
        periods += 1
    return tuple(sorted(set(dates)))


# --------------------------------------------------------------------------- #
# Weekday weighting and settlement
# --------------------------------------------------------------------------- #


def weekday_weights(
    days: Sequence[date],
    weights_bps: Sequence[int] = DEFAULT_RECEIPT_WEIGHTS_BPS,
) -> tuple[int, ...]:
    """Per-day allocation ratios for `days`, by weekday.

    `weights_bps` is indexed by weekday starting Monday. Days beyond the supplied
    weights (i.e. the weekend) get zero, which is the whole point: receipts do
    not arrive on a Saturday.
    """
    if any(weight < 0 for weight in weights_bps):
        raise CadenceError("weekday weights must be non-negative")
    ratios = tuple(
        weights_bps[day.weekday()] if day.weekday() < len(weights_bps) else 0 for day in days
    )
    if sum(ratios) == 0:
        raise CadenceError("weekday weights sum to zero for the supplied days")
    return ratios


def distribute_over_week(
    amount: Money,
    week_start_day: date,
    calendar: BusinessCalendar,
    *,
    weights_bps: Sequence[int] = DEFAULT_RECEIPT_WEIGHTS_BPS,
) -> tuple[tuple[date, Money], ...]:
    """Split a weekly amount across the week's business days.

    Uses `Money.allocate`, so the parts sum back to `amount` exactly — no minor
    unit is created or lost by the weighting.
    """
    if week_start_day.weekday() != MONDAY:
        raise CadenceError(f"{week_start_day} is not a Monday")
    days = calendar.business_days_between(week_start_day, week_start_day + timedelta(days=6))
    if not days:
        raise CadenceError(f"week of {week_start_day} has no business days")
    ratios = weekday_weights(days, weights_bps)
    parts = amount.allocate(ratios)
    return tuple(zip(days, parts, strict=True))


def settlement_date(
    initiated: date,
    calendar: BusinessCalendar,
    *,
    lag_business_days: int = 1,
) -> date:
    """When a payment initiated on `initiated` actually lands.

    A payment dated the Friday before a Monday holiday settles Tuesday — the
    example `WORKFLOW.md` §3 calls out.
    """
    return calendar.add_business_days(initiated, lag_business_days)


def allocate_by_weight(amount: Money, weights: Sequence[int]) -> tuple[Money, ...]:
    """Weighted split with no rounding leakage, for callers outside a week."""
    if not weights:
        raise MoneyError("allocate_by_weight requires at least one weight")
    return amount.allocate(weights)


__all__ = [
    "DEFAULT_RECEIPT_WEIGHTS_BPS",
    "DEFAULT_WEEKEND",
    "FRIDAY",
    "MONDAY",
    "SATURDAY",
    "SUNDAY",
    "THURSDAY",
    "TUESDAY",
    "WEDNESDAY",
    "BusinessCalendar",
    "CadenceError",
    "Convention",
    "DepositSchedule",
    "PayrollFrequency",
    "ServiceFrequency",
    "add_months",
    "add_months_eom",
    "allocate_by_weight",
    "ap_run_dates",
    "biweekly_pay_dates",
    "debt_service_dates",
    "distribute_over_week",
    "month_starts",
    "monthly_dates",
    "pay_dates",
    "payroll_tax_deposit_dates",
    "quarterly_estimated_tax_dates",
    "semi_monthly_pay_dates",
    "settlement_date",
    "three_payroll_months",
    "us_calendar",
    "us_federal_holidays",
    "week_end",
    "week_index",
    "week_start",
    "week_starts",
    "weekday_weights",
]
