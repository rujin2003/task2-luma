"""Forecast engine tests.

The exit criterion in `PHASES.md` is "a forecast reproduces a hand-worked 13-week
grid exactly", so the central test here states the expected dates and amounts
literally — worked out from the 2026 calendar — rather than comparing the engine
against itself. Everything else checks one driver at a time.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from backend.finance import cadence, forecast
from backend.finance.cadence import DepositSchedule, PayrollFrequency, ServiceFrequency
from backend.finance.errors import PolicyError
from backend.finance.forecast import (
    CATEGORIES,
    CollectionCurve,
    DatedAmount,
    DebtServicePlan,
    ForecastInputs,
    ForecastOverride,
    LagComponent,
    OpenInvoice,
    OpenPayable,
    PayrollPlan,
    SubscriptionBilling,
)
from backend.finance.money import Money
from backend.models.vocab import FORECAST_CATEGORIES

CAL = cadence.us_calendar(2025, 2028)
AS_OF = datetime(2026, 9, 7, tzinfo=UTC)
FIRST_WEEK = date(2026, 9, 7)
WEEKS = cadence.week_starts(FIRST_WEEK, 13)


def usd(major: int) -> Money:
    return Money.of(major * 100, "USD")


def week_of(day: date) -> int:
    return cadence.week_index(day, FIRST_WEEK)


def base_inputs(**overrides: object) -> ForecastInputs:
    defaults: dict[str, object] = {
        "version_id": "fv-test",
        "as_of": AS_OF,
        "first_week_start": FIRST_WEEK,
        "opening_cash": usd(20_000_000),
        "calendar": CAL,
        "undrawn_revolver": usd(25_000_000),
    }
    defaults.update(overrides)
    return ForecastInputs(**defaults)  # type: ignore[arg-type]


SIMPLE_CURVE = CollectionCurve(
    lag_mixture={"default": (LagComponent(weight_bps=10_000, lag_days=0),)},
    collection_probability_bps={
        "current": 10_000,
        "1-30": 10_000,
        "31-60": 10_000,
        "61-90": 10_000,
        "90+": 10_000,
    },
)


# --------------------------------------------------------------------------- #
# Structure
# --------------------------------------------------------------------------- #


def test_category_set_matches_the_persisted_vocabulary() -> None:
    """`backend/finance` duplicates the list; this is what stops it drifting."""
    assert CATEGORIES == FORECAST_CATEGORIES


def test_every_category_gets_a_cell_in_every_week() -> None:
    """A missing cell and a zero cell mean different things to a bridge."""
    result = forecast.build(base_inputs())
    assert len(result.cells) == len(CATEGORIES) * 13
    assert result.weeks == WEEKS
    empty = result.cell("rent_leases", 2)
    assert empty.amount == usd(0)
    assert empty.method == "no_activity"
    assert "none falls in this week" in empty.assumption


def test_an_empty_forecast_leaves_cash_flat() -> None:
    result = forecast.build(base_inputs())
    assert result.net_change() == usd(0)
    assert result.closing_cash_by_week[-1] == usd(20_000_000)
    assert result.available_liquidity_by_week[0] == usd(45_000_000)
    assert result.minimum_cash() == usd(20_000_000)
    assert result.minimum_cash_week() == 1


def test_inputs_validate_their_own_shape() -> None:
    with pytest.raises(PolicyError, match="not a Monday"):
        base_inputs(first_week_start=date(2026, 9, 8))
    with pytest.raises(PolicyError, match="horizon_weeks must be positive"):
        base_inputs(horizon_weeks=0)
    with pytest.raises(PolicyError, match="share a currency"):
        base_inputs(undrawn_revolver=Money.of(1, "EUR"))


def test_aging_buckets() -> None:
    assert forecast.aging_bucket(-5) == "current"
    assert forecast.aging_bucket(0) == "current"
    assert forecast.aging_bucket(30) == "1-30"
    assert forecast.aging_bucket(31) == "31-60"
    assert forecast.aging_bucket(90) == "61-90"
    assert forecast.aging_bucket(91) == "90+"


# --------------------------------------------------------------------------- #
# Drivers, one at a time
# --------------------------------------------------------------------------- #


def test_payroll_lands_on_pay_dates_not_as_a_monthly_average() -> None:
    result = forecast.build(
        base_inputs(
            payroll=PayrollPlan(
                salaried_gross_per_period=usd(2_000_000),
                hourly_gross_per_period=usd(0),
                frequency=PayrollFrequency.SEMI_MONTHLY,
            )
        )
    )
    # 2026 semi-monthly pay dates inside the horizon, paid early when closed.
    expected = {
        date(2026, 9, 15),
        date(2026, 9, 30),
        date(2026, 10, 15),
        date(2026, 10, 30),  # the 31st is a Saturday
        date(2026, 11, 13),  # the 15th is a Sunday, the 14th a Saturday
        date(2026, 11, 30),
    }
    paid_weeks = {week_of(day) for day in expected}
    for index in range(1, 14):
        cell = result.cell("payroll_salaried", index)
        if index in paid_weeks:
            assert cell.amount == usd(-2_000_000), index
        else:
            assert cell.amount == usd(0), index
    assert result.category_total("payroll_salaried") == usd(-12_000_000)


def test_three_biweekly_payrolls_fall_in_one_month() -> None:
    """The cash surprise a monthly average cannot produce."""
    result = forecast.build(
        base_inputs(
            payroll=PayrollPlan(
                salaried_gross_per_period=usd(1_000_000),
                hourly_gross_per_period=usd(0),
                frequency=PayrollFrequency.BIWEEKLY,
                salaried_anchor=date(2026, 10, 2),
            )
        )
    )
    by_month: dict[int, int] = {}
    for cell in result.cells:
        if cell.category != "payroll_salaried":
            continue
        for contribution in cell.contributions:
            month = contribution.event_date.month
            by_month[month] = by_month.get(month, 0) + 1
    # Biweekly on a 2026-10-02 anchor: October takes three pay dates (2, 16, 30)
    # against November's two. No monthly average can produce that.
    assert by_month[10] == 3
    assert by_month[11] == 2


def test_payroll_taxes_are_a_separate_line_on_a_separate_date() -> None:
    result = forecast.build(
        base_inputs(
            payroll=PayrollPlan(
                salaried_gross_per_period=usd(2_000_000),
                hourly_gross_per_period=usd(0),
                employer_tax_bps=765,  # employer FICA
                employee_withholding_bps=1_800,
                deposit_schedule=DepositSchedule.SEMI_WEEKLY,
            )
        )
    )
    # Payroll on Tuesday 2026-09-15 deposits Friday 2026-09-18 — a different
    # week from the 2026-09-30 payroll's 2026-10-07 deposit.
    tax_week = week_of(date(2026, 9, 18))
    assert result.cell("payroll_taxes_benefits", tax_week).amount == usd(-513_000)
    # 2565 bps of $2,000,000 = $513,000.
    assert result.cell("payroll_salaried", week_of(date(2026, 9, 15))).amount == usd(-2_000_000)
    # The tax lands in a week of its own, not folded into payroll.
    assert result.cell("payroll_taxes_benefits", week_of(date(2026, 9, 15))).amount != usd(0) or (
        week_of(date(2026, 9, 18)) != week_of(date(2026, 9, 15))
    )


def test_benefits_fund_with_payroll() -> None:
    result = forecast.build(
        base_inputs(
            payroll=PayrollPlan(
                salaried_gross_per_period=usd(1_000_000),
                hourly_gross_per_period=usd(0),
                benefits_per_period=usd(120_000),
            )
        )
    )
    assert result.cell("payroll_taxes_benefits", week_of(date(2026, 9, 15))).amount == usd(-120_000)


def test_hourly_payroll_runs_biweekly_on_its_own_anchor() -> None:
    result = forecast.build(
        base_inputs(
            payroll=PayrollPlan(
                salaried_gross_per_period=usd(0),
                hourly_gross_per_period=usd(400_000),
                hourly_anchor=date(2026, 9, 11),
            )
        )
    )
    assert result.cell("payroll_hourly", week_of(date(2026, 9, 11))).amount == usd(-400_000)
    assert result.cell("payroll_hourly", week_of(date(2026, 9, 25))).amount == usd(-400_000)
    assert result.cell("payroll_hourly", week_of(date(2026, 9, 18))).amount == usd(0)


def test_hourly_payroll_requires_an_anchor() -> None:
    with pytest.raises(PolicyError, match="hourly payroll requires an anchor"):
        forecast.build(
            base_inputs(
                payroll=PayrollPlan(
                    salaried_gross_per_period=usd(0),
                    hourly_gross_per_period=usd(1),
                    frequency=PayrollFrequency.SEMI_MONTHLY,
                )
            )
        )


def test_ap_is_a_thursday_spike_not_a_flat_line() -> None:
    payables = (
        OpenPayable("VI-1", "Vendor A", usd(400_000), date(2026, 9, 9)),
        OpenPayable("VI-2", "Vendor B", usd(250_000), date(2026, 9, 10)),
        OpenPayable("VI-3", "Vendor C", usd(600_000), date(2026, 9, 21)),
    )
    result = forecast.build(base_inputs(open_payables=payables))
    # VI-1 and VI-2 both catch the 2026-09-10 run; VI-3 catches 2026-09-24.
    assert result.cell("ap_trade", week_of(date(2026, 9, 10))).amount == usd(-650_000)
    assert result.cell("ap_trade", week_of(date(2026, 9, 24))).amount == usd(-600_000)
    assert result.cell("ap_trade", week_of(date(2026, 9, 17))).amount == usd(0)


def test_an_overdue_payable_catches_the_next_run_not_a_past_one() -> None:
    result = forecast.build(
        base_inputs(
            open_payables=(OpenPayable("VI-9", "Vendor Z", usd(100_000), date(2026, 8, 1)),)
        )
    )
    assert result.cell("ap_trade", 1).amount == usd(-100_000)


def test_receipts_follow_the_collection_curve_as_a_mixture() -> None:
    """Bimodal, not a mean: the two modes land in different weeks."""
    curve = CollectionCurve(
        lag_mixture={
            "default": (
                LagComponent(weight_bps=7_000, lag_days=3),
                LagComponent(weight_bps=3_000, lag_days=45),
            )
        },
        collection_probability_bps={
            "current": 9_800,
            "1-30": 9_500,
            "31-60": 9_000,
            "61-90": 7_500,
            "90+": 4_000,
        },
    )
    result = forecast.build(
        base_inputs(
            collection_curve=curve,
            open_invoices=(OpenInvoice("INV-1", "Customer A", usd(1_000_000), date(2026, 9, 14)),),
        )
    )
    # Not yet due on 2026-09-07, so the 'current' bucket: 9800 bps collectible
    # of $1,000,000 is $980,000, split 70/30 across the two lag modes.
    near = result.cell("receipts_trade_ar", week_of(date(2026, 9, 17)))
    far = result.cell("receipts_trade_ar", week_of(date(2026, 10, 29)))
    assert near.amount == usd(686_000)
    assert far.amount == usd(294_000)
    assert result.category_total("receipts_trade_ar") == usd(980_000)
    assert "9800 bps collectible" in near.assumption


def test_a_promise_to_pay_overrides_the_fitted_curve() -> None:
    result = forecast.build(
        base_inputs(
            collection_curve=SIMPLE_CURVE,
            open_invoices=(
                OpenInvoice(
                    "INV-1832",
                    "Customer A",
                    usd(900_000),
                    date(2026, 8, 15),
                    promise_to_pay=date(2026, 9, 21),
                ),
            ),
        )
    )
    cell = result.cell("receipts_trade_ar", week_of(date(2026, 9, 21)))
    assert cell.amount == usd(900_000)
    assert cell.method == "promise_to_pay"
    assert "promised payment on 2026-09-21" in cell.assumption


def test_a_disputed_invoice_is_haircut() -> None:
    curve = CollectionCurve(
        lag_mixture={"default": (LagComponent(10_000, 0),)},
        collection_probability_bps=dict.fromkeys(forecast.AGING_BUCKETS, 10_000),
        dispute_haircut_bps=2_500,
    )
    result = forecast.build(
        base_inputs(
            collection_curve=curve,
            open_invoices=(
                OpenInvoice("INV-D", "Customer D", usd(400_000), date(2026, 9, 14), disputed=True),
            ),
        )
    )
    assert result.category_total("receipts_trade_ar") == usd(100_000)


def test_a_worthless_invoice_produces_no_line() -> None:
    curve = CollectionCurve(
        lag_mixture={"default": (LagComponent(10_000, 0),)},
        collection_probability_bps=dict.fromkeys(forecast.AGING_BUCKETS, 0),
    )
    result = forecast.build(
        base_inputs(
            collection_curve=curve,
            open_invoices=(
                OpenInvoice("INV-X", "Customer X", usd(400_000), date(2026, 9, 14)),
                OpenInvoice("INV-Y", "Customer Y", usd(0), date(2026, 9, 14)),
            ),
        )
    )
    assert result.category_total("receipts_trade_ar") == usd(0)


def test_a_segment_mixture_is_used_when_present() -> None:
    curve = CollectionCurve(
        lag_mixture={
            "default": (LagComponent(10_000, 0),),
            "enterprise": (LagComponent(10_000, 30),),
        },
        collection_probability_bps=dict.fromkeys(forecast.AGING_BUCKETS, 10_000),
    )
    result = forecast.build(
        base_inputs(
            collection_curve=curve,
            open_invoices=(
                OpenInvoice(
                    "INV-E", "Big Co", usd(100_000), date(2026, 9, 14), segment="enterprise"
                ),
                OpenInvoice("INV-S", "Small Co", usd(100_000), date(2026, 9, 14), segment="smb"),
            ),
        )
    )
    assert result.cell("receipts_trade_ar", week_of(date(2026, 9, 14))).amount == usd(100_000)
    assert result.cell("receipts_trade_ar", week_of(date(2026, 10, 14))).amount == usd(100_000)


def test_collection_curve_validates_itself() -> None:
    with pytest.raises(PolicyError, match="'default' lag mixture"):
        CollectionCurve(lag_mixture={}, collection_probability_bps={})
    with pytest.raises(PolicyError, match="is empty"):
        CollectionCurve(lag_mixture={"default": ()}, collection_probability_bps={})
    with pytest.raises(PolicyError, match="does not sum to 10000"):
        CollectionCurve(
            lag_mixture={"default": (LagComponent(5_000, 0),)}, collection_probability_bps={}
        )
    with pytest.raises(PolicyError, match="missing buckets"):
        CollectionCurve(
            lag_mixture={"default": (LagComponent(10_000, 0),)},
            collection_probability_bps={"current": 10_000},
        )


def test_subscription_cash_lands_on_the_payout_date_not_the_charge_date() -> None:
    """A successful payment is not cash (`DATA_SOURCES.md` §6, Insight #2)."""
    result = forecast.build(
        base_inputs(
            subscriptions=(
                SubscriptionBilling("sub-1", "Customer A", usd(100_000), date(2026, 9, 11)),
            ),
            processor_success_rate_bps=9_410,
            processor_payout_lag_days=3,
        )
    )
    # Charged Friday 2026-09-11; three business days later is Wednesday the 16th,
    # which is the following week.
    assert week_of(date(2026, 9, 11)) == 1
    assert result.cell("receipts_subscription", 1).amount == usd(0)
    assert result.cell("receipts_subscription", 2).amount == usd(94_100)
    assert (
        "settling 3 business days later at payout"
        in result.cell("receipts_subscription", 2).assumption
    )


def test_subscription_intervals_and_inactive_rows() -> None:
    result = forecast.build(
        base_inputs(
            subscriptions=(
                SubscriptionBilling("sub-m", "A", usd(10_000), date(2026, 9, 8)),
                SubscriptionBilling(
                    "sub-q", "B", usd(90_000), date(2026, 9, 8), interval=ServiceFrequency.QUARTERLY
                ),
                SubscriptionBilling(
                    "sub-a",
                    "C",
                    usd(500_000),
                    date(2026, 9, 8),
                    interval=ServiceFrequency.ANNUAL,
                ),
                SubscriptionBilling(
                    "sub-x", "D", usd(999_999), date(2026, 9, 8), status="cancelled"
                ),
                SubscriptionBilling("sub-z", "E", usd(0), date(2026, 9, 8)),
            )
        )
    )
    # Three monthly payouts settle inside the horizon: the 2026-12-08 charge
    # pays out on the 10th, four days past the 2026-12-06 horizon end, so it
    # belongs to a later version. Quarterly and annual bill once each here.
    # Cancelled and zero-amount rows contribute nothing at all.
    monthly = sum(
        1
        for cell in result.cells
        if cell.category == "receipts_subscription"
        for contribution in cell.contributions
        if contribution.reference == "sub-m"
    )
    assert monthly == 3
    assert result.category_total("receipts_subscription") == usd(10_000 * 3 + 90_000 + 500_000)


def test_rent_and_taxes_land_on_their_statutory_dates() -> None:
    result = forecast.build(
        base_inputs(
            rent_per_month=usd(300_000),
            estimated_tax_per_quarter=usd(1_500_000),
            sales_tax=(DatedAmount("ST-09", usd(-85_000), date(2026, 9, 21), "September VAT"),),
        )
    )
    # Rent: 2026-10-01 and 2026-11-02 (the 1st is a Sunday); 2026-09-01 predates
    # the horizon and 2026-12-01 is inside it.
    assert result.cell("rent_leases", week_of(date(2026, 10, 1))).amount == usd(-300_000)
    assert result.cell("rent_leases", week_of(date(2026, 11, 2))).amount == usd(-300_000)
    assert result.cell("rent_leases", week_of(date(2026, 12, 1))).amount == usd(-300_000)
    assert result.category_total("rent_leases") == usd(-900_000)
    # Estimated tax: only 15 September falls inside a horizon ending 2026-12-06.
    # The December instalment belongs to a later version, not to this one.
    assert result.cell("tax_income_estimated", week_of(date(2026, 9, 15))).amount == usd(-1_500_000)
    assert result.category_total("tax_income_estimated") == usd(-1_500_000)
    assert result.cell("tax_sales_vat", week_of(date(2026, 9, 21))).amount == usd(-85_000)


def test_debt_service_produces_interest_and_principal_on_the_same_date() -> None:
    result = forecast.build(
        base_inputs(
            debt_service=(
                DebtServicePlan(
                    facility_id="rev-1",
                    name="Revolver",
                    drawn=usd(10_000_000),
                    all_in_rate_bps=658,
                    first_service_date=date(2026, 9, 30),
                    principal_per_period=usd(500_000),
                ),
            )
        )
    )
    week = week_of(date(2026, 9, 30))
    # Actual/360 on $10,000,000 at 658 bps for a 90-day quarter.
    assert result.cell("debt_interest", week).amount == Money.of(-16_450_000, "USD")
    assert result.cell("debt_principal", week).amount == usd(-500_000)
    assert result.cell("debt_principal", week + 1).amount == usd(0)


def test_capex_and_insurance_are_dated_commitments() -> None:
    result = forecast.build(
        base_inputs(
            capex=(DatedAmount("PO-77", usd(-750_000), date(2026, 10, 6), "DC build milestone 2"),),
            insurance_software=(
                DatedAmount("REN-1", usd(-420_000), date(2026, 11, 2), "D&O renewal"),
            ),
            other_receipts=(
                DatedAmount("REF-1", usd(310_000), date(2026, 9, 24), "state tax refund"),
            ),
        )
    )
    assert result.cell("capex", week_of(date(2026, 10, 6))).amount == usd(-750_000)
    assert result.cell("insurance_software", week_of(date(2026, 11, 2))).amount == usd(-420_000)
    other = result.cell("receipts_other", week_of(date(2026, 9, 24)))
    assert other.amount == usd(310_000)
    assert other.assumption == "state tax refund"


def test_activity_outside_the_horizon_is_discarded() -> None:
    result = forecast.build(
        base_inputs(
            capex=(
                DatedAmount("PO-early", usd(-1), date(2026, 9, 6)),
                DatedAmount("PO-late", usd(-1), date(2026, 12, 7)),
                DatedAmount("PO-zero", usd(0), date(2026, 10, 1)),
            )
        )
    )
    assert result.category_total("capex") == usd(0)


# --------------------------------------------------------------------------- #
# Overrides
# --------------------------------------------------------------------------- #


def test_an_override_replaces_a_cell_and_records_its_reason() -> None:
    inputs = base_inputs(
        collection_curve=SIMPLE_CURVE,
        open_invoices=(OpenInvoice("INV-1", "Customer A", usd(500_000), date(2026, 9, 14)),),
        overrides=(
            ForecastOverride(
                category="receipts_trade_ar",
                week_index=2,
                amount=usd(200_000),
                reason="Customer A confirmed a partial payment only",
                author="a.rivera",
            ),
        ),
    )
    result = forecast.build(inputs)
    cell = result.cell("receipts_trade_ar", 2)
    assert cell.amount == usd(200_000)
    assert cell.method == "override"
    assert cell.override_reason == "Customer A confirmed a partial payment only"
    # The engine's own number is preserved in the assumption, so the override is
    # auditable rather than destructive.
    assert "engine computed 500000.00 USD" in cell.assumption
    assert "a.rivera" in cell.assumption


def test_override_guards() -> None:
    with pytest.raises(PolicyError, match="unknown category"):
        ForecastOverride("not_a_category", 1, usd(1), "r", "a")
    with pytest.raises(PolicyError, match="1-based"):
        ForecastOverride("capex", 0, usd(1), "r", "a")
    with pytest.raises(PolicyError, match="outside the horizon"):
        forecast.build(base_inputs(overrides=(ForecastOverride("capex", 14, usd(1), "r", "a"),)))
    with pytest.raises(PolicyError, match="is in EUR"):
        forecast.build(
            base_inputs(overrides=(ForecastOverride("capex", 1, Money.of(1, "EUR"), "r", "a"),))
        )


# --------------------------------------------------------------------------- #
# Rolling
# --------------------------------------------------------------------------- #


def test_rolling_drops_week_one_and_adds_week_fourteen() -> None:
    inputs = base_inputs(rent_per_month=usd(300_000))
    first = forecast.build(inputs)
    rolled = forecast.build(
        forecast.roll_forward(
            inputs,
            version_id="fv-test-2",
            as_of=AS_OF + timedelta(weeks=1),
            opening_cash=first.closing_cash_by_week[0],
        )
    )
    assert rolled.weeks[0] == first.weeks[1]
    assert rolled.weeks[-1] == first.weeks[-1] + timedelta(weeks=1)
    assert rolled.version_id == "fv-test-2"
    # The rolled version's closing cash still lands on the same total, because
    # week 1's activity is now in its opening balance.
    assert rolled.closing_cash_by_week[-1] == first.closing_cash_by_week[-1] - usd(0)


def test_an_override_is_not_carried_into_the_next_cycle() -> None:
    """A judgement about one week must not silently become a standing assumption."""
    inputs = base_inputs(
        overrides=(ForecastOverride("capex", 3, usd(-1_000_000), "one-off", "a.rivera"),)
    )
    rolled = forecast.roll_forward(
        inputs, version_id="v2", as_of=AS_OF, opening_cash=usd(20_000_000)
    )
    assert rolled.overrides == ()


# --------------------------------------------------------------------------- #
# The hand-worked grid
# --------------------------------------------------------------------------- #


def hand_worked_inputs() -> ForecastInputs:
    """NovaTech-shaped, but small enough to verify with a pen."""
    return base_inputs(
        opening_cash=usd(20_000_000),
        undrawn_revolver=usd(25_000_000),
        collection_curve=SIMPLE_CURVE,
        open_invoices=(
            OpenInvoice("INV-1832", "Customer A", usd(900_000), date(2026, 9, 16)),
            OpenInvoice("INV-1901", "Customer B", usd(640_000), date(2026, 10, 14)),
        ),
        subscriptions=(SubscriptionBilling("sub-1", "Customer C", usd(700_000), date(2026, 9, 8)),),
        processor_success_rate_bps=10_000,
        processor_payout_lag_days=2,
        payroll=PayrollPlan(
            salaried_gross_per_period=usd(2_100_000),
            hourly_gross_per_period=usd(0),
            frequency=PayrollFrequency.SEMI_MONTHLY,
            employer_tax_bps=765,
        ),
        open_payables=(OpenPayable("VI-44", "Vendor A", usd(1_800_000), date(2026, 9, 15)),),
        rent_per_month=usd(300_000),
        estimated_tax_per_quarter=usd(1_500_000),
    )


def test_forecast_reproduces_a_hand_worked_grid_exactly() -> None:
    """Every non-zero cell, stated as a date and an amount worked out by hand.

    The dates come from the 2026 calendar: semi-monthly payroll paid early when
    closed, employment tax on the IRS semi-weekly schedule, AP on the Thursday
    run following the due date, rent on the 1st rolled forward, estimated tax on
    15 September and 15 December, and processor receipts two business days after
    the charge.
    """
    result = forecast.build(hand_worked_inputs())

    expected: dict[tuple[str, date], Money] = {
        # Receipts: both invoices collect on their due date under the flat curve.
        ("receipts_trade_ar", date(2026, 9, 16)): usd(900_000),
        ("receipts_trade_ar", date(2026, 10, 14)): usd(640_000),
        # Subscription: charged the 8th of each month; payout two business days
        # later, so October clears on the 13th because the 12th is Columbus Day.
        ("receipts_subscription", date(2026, 9, 10)): usd(700_000),
        ("receipts_subscription", date(2026, 10, 13)): usd(700_000),
        ("receipts_subscription", date(2026, 11, 10)): usd(700_000),
        # Payroll: six semi-monthly dates inside the horizon.
        ("payroll_salaried", date(2026, 9, 15)): usd(-2_100_000),
        ("payroll_salaried", date(2026, 9, 30)): usd(-2_100_000),
        ("payroll_salaried", date(2026, 10, 15)): usd(-2_100_000),
        ("payroll_salaried", date(2026, 10, 30)): usd(-2_100_000),
        ("payroll_salaried", date(2026, 11, 13)): usd(-2_100_000),
        ("payroll_salaried", date(2026, 11, 30)): usd(-2_100_000),
        # Employment tax: 765 bps of $2,100,000 = $160,650, on its own schedule.
        ("payroll_taxes_benefits", date(2026, 9, 18)): usd(-160_650),
        ("payroll_taxes_benefits", date(2026, 10, 7)): usd(-160_650),
        ("payroll_taxes_benefits", date(2026, 10, 21)): usd(-160_650),
        ("payroll_taxes_benefits", date(2026, 11, 4)): usd(-160_650),
        ("payroll_taxes_benefits", date(2026, 11, 18)): usd(-160_650),
        ("payroll_taxes_benefits", date(2026, 12, 4)): usd(-160_650),
        # AP: due the 15th, paid in the 2026-09-17 run.
        ("ap_trade", date(2026, 9, 17)): usd(-1_800_000),
        # Rent on the 1st, rolled forward when closed.
        ("rent_leases", date(2026, 10, 1)): usd(-300_000),
        ("rent_leases", date(2026, 11, 2)): usd(-300_000),
        ("rent_leases", date(2026, 12, 1)): usd(-300_000),
        # Quarterly estimated tax.
        ("tax_income_estimated", date(2026, 9, 15)): usd(-1_500_000),
    }

    by_cell: dict[tuple[str, int], Money] = {}
    for (category, day), amount in expected.items():
        key = (category, week_of(day))
        by_cell[key] = by_cell.get(key, usd(0)) + amount

    for category in CATEGORIES:
        for index in range(1, 14):
            actual = result.cell(category, index).amount
            assert actual == by_cell.get((category, index), usd(0)), (
                f"{category} week {index} ({WEEKS[index - 1]})"
            )

    # And the running balances the grid closes on.
    net = Money.sum(expected.values(), "USD")
    assert result.net_change() == net
    assert result.closing_cash_by_week[-1] == usd(20_000_000) + net
    assert result.available_liquidity_by_week[-1] == (usd(20_000_000) + net + usd(25_000_000))


def test_the_trough_is_dated_not_a_vibe() -> None:
    result = forecast.build(hand_worked_inputs())
    trough_week = result.minimum_cash_week()
    assert result.closing_cash_by_week[trough_week - 1] == result.minimum_cash()
    assert result.minimum_liquidity() == result.minimum_cash() + usd(25_000_000)


def test_thirty_day_liquidity_uses_whole_weeks() -> None:
    result = forecast.build(hand_worked_inputs())
    assert result.liquidity_within(30) == min(result.available_liquidity_by_week[:4])
    with pytest.raises(PolicyError, match="at least one week"):
        result.liquidity_within(3)


def test_inflow_outflow_split_and_category_totals() -> None:
    result = forecast.build(hand_worked_inputs())
    inflows, outflows = forecast.inflow_outflow(result)
    assert inflows == usd(900_000 + 640_000 + 2_100_000)
    assert outflows.amount < 0
    assert inflows + outflows == result.net_change()
    totals = forecast.category_totals(result)
    assert set(totals) == set(CATEGORIES)


def test_unknown_cell_lookup_raises() -> None:
    result = forecast.build(base_inputs())
    with pytest.raises(KeyError):
        result.cell("capex", 99)


# --------------------------------------------------------------------------- #
# Contract view and provenance
# --------------------------------------------------------------------------- #


def test_grid_dto_carries_provenance_to_a_real_source_row() -> None:
    result = forecast.build(hand_worked_inputs())
    grid = result.to_grid()
    assert grid.version_id == "fv-test"
    assert len(grid.lines) == len(CATEGORIES) * 13
    ar_line = next(
        line
        for line in grid.lines
        if line.category == "receipts_trade_ar" and line.amount.amount != 0
    )
    assert ar_line.provenance.source_table == "invoices"
    assert ar_line.provenance.source_pk == "INV-1832"
    assert ar_line.source == "ar_aging"
    # An empty cell is provenanced to the engine's own computation, honestly.
    empty_line = next(
        line for line in grid.lines if line.category == "capex" and line.amount.amount == 0
    )
    assert empty_line.provenance.source_table == "forecast_lines"


def test_provenance_names_the_dominant_contributor() -> None:
    result = forecast.build(
        base_inputs(
            open_payables=(
                OpenPayable("VI-small", "Vendor A", usd(10_000), date(2026, 9, 9)),
                OpenPayable("VI-large", "Vendor B", usd(900_000), date(2026, 9, 9)),
            )
        )
    )
    cell = result.cell("ap_trade", week_of(date(2026, 9, 10)))
    assert len(cell.contributions) == 2
    provenance = cell.provenance(AS_OF, AS_OF)
    assert provenance.source_pk == "VI-large"


def test_assumption_summary_collapses_repeats_and_counts_the_tail() -> None:
    payables = tuple(
        OpenPayable(f"VI-{index}", f"Vendor {index}", usd(1_000), date(2026, 9, 9))
        for index in range(5)
    )
    result = forecast.build(base_inputs(open_payables=payables))
    cell = result.cell("ap_trade", week_of(date(2026, 9, 10)))
    assert "3 further item(s)" in cell.assumption


def test_render_grid_is_stable_text() -> None:
    result = forecast.build(hand_worked_inputs())
    rendered = forecast.render_grid(result, weeks=3)
    assert rendered.splitlines()[0].startswith("category")
    assert "closing cash" in rendered
    assert "available liquidity" in rendered
    for category in CATEGORIES:
        assert category in rendered


def test_build_is_deterministic() -> None:
    """Same inputs, same numbers — the premise of the whole product."""
    first = forecast.build(hand_worked_inputs())
    second = forecast.build(hand_worked_inputs())
    assert forecast.render_grid(first) == forecast.render_grid(second)
    assert first.cells == second.cells
