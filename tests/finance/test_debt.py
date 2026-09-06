"""Facility capacity, draw cost and amortisation."""

from __future__ import annotations

from datetime import date

import pytest

from backend.finance import cadence, debt
from backend.finance.cadence import ServiceFrequency
from backend.finance.debt import Facility
from backend.finance.errors import PolicyError
from backend.finance.money import Money
from backend.finance.policy import TreasuryPolicy
from backend.finance.rounding import Rounding

POLICY = TreasuryPolicy.load()
CAL = cadence.us_calendar(2025, 2032)


def usd(major: int) -> Money:
    return Money.of(major * 100, "USD")


def revolver(**overrides: object) -> Facility:
    defaults: dict[str, object] = {
        "facility_id": "rev-1",
        "name": "Senior Secured Revolver",
        "limit": usd(50_000_000),
        "drawn": usd(10_000_000),
        # SOFR plus a credit spread, per `DATA_SOURCES.md` §2.
        "base_rate_bps": 433,
        "spread_bps": 225,
        "maturity": date(2029, 6, 30),
        "commitment_fee_bps": 35,
        "max_utilization_bps": 8_000,
    }
    defaults.update(overrides)
    return Facility(**defaults)  # type: ignore[arg-type]


def test_facility_validates_its_own_shape() -> None:
    with pytest.raises(PolicyError, match="currencies differ"):
        revolver(drawn=Money.of(1, "EUR"))
    with pytest.raises(PolicyError, match="non-negative"):
        revolver(drawn=usd(-1))
    with pytest.raises(PolicyError, match="exceeds limit"):
        revolver(drawn=usd(60_000_000))
    with pytest.raises(PolicyError, match="max_utilization_bps"):
        revolver(max_utilization_bps=10_001)


def test_utilization_and_undrawn() -> None:
    facility = revolver()
    assert facility.undrawn == usd(40_000_000)
    assert debt.utilization_bps(facility) == 2_000
    assert debt.utilization_bps(revolver(limit=usd(0), drawn=usd(0))) == 0


def test_draw_cost_excludes_the_commitment_fee() -> None:
    """The fee is paid on undrawn balance whether or not we draw — not a draw cost."""
    facility = revolver()
    assert debt.all_in_draw_cost_bps(facility) == 658


def test_max_safe_draw_is_bounded_by_the_tighter_ceiling() -> None:
    facility = revolver()
    # Facility allows 80%; TreasuryPolicy allows 70%. Policy binds.
    assert debt.effective_utilization_ceiling(facility, POLICY) == 7_000
    # 70% of $50M is $35M; $10M is already drawn.
    assert debt.max_safe_draw(facility, POLICY) == usd(25_000_000)
    # Undrawn capacity is a bigger, and wrong, number to recommend against.
    assert facility.undrawn == usd(40_000_000)


def test_a_facility_already_over_its_ceiling_has_zero_safe_capacity() -> None:
    """Not negative capacity — the overage is a covenant finding, not a draw."""
    assert debt.max_safe_draw(revolver(drawn=usd(40_000_000)), POLICY) == usd(0)


def test_capacity_contract_view() -> None:
    view = debt.capacity(revolver(), POLICY)
    assert view.facility_id == "rev-1"
    assert view.utilization_bps == 2_000
    assert view.all_in_draw_cost_bps == 658
    assert view.max_safe_draw.amount == usd(25_000_000).amount
    assert view.commitment_fee_bps == 35


def test_interest_is_actual_over_360() -> None:
    # $10,000,000.00 at 658 bps for 90 days = 10_000_000 * 0.0658 * 90/360.
    interest = debt.accrued_interest(usd(10_000_000), 658, 90)
    assert interest == Money.of(16_450_000, "USD")  # $164,500.00
    assert debt.accrued_interest(usd(10_000_000), 658, 0) == usd(0)


def test_interest_guards() -> None:
    with pytest.raises(PolicyError, match="negative number of days"):
        debt.accrued_interest(usd(1), 100, -1)
    with pytest.raises(PolicyError, match="rate cannot be negative"):
        debt.accrued_interest(usd(1), -1, 30)


def test_rounding_mode_is_explicit() -> None:
    # A one-day accrual on a small balance rounds rather than truncating silently.
    assert debt.accrued_interest(Money.of(1_000, "USD"), 500, 1, rounding=Rounding.DOWN) == (
        Money.of(0, "USD")
    )
    assert debt.accrued_interest(Money.of(1_000_000, "USD"), 500, 1) == Money.of(139, "USD")


def test_draw_cost_and_commitment_fee() -> None:
    facility = revolver()
    assert debt.draw_cost(facility, usd(2_000_000), 14) == Money.of(511_778, "USD")
    # The fee accrues on $40M undrawn at 35 bps for 90 days.
    assert debt.commitment_fee(facility, 90) == Money.of(3_500_000, "USD")
    with pytest.raises(PolicyError, match="does not match the facility"):
        debt.draw_cost(facility, Money.of(1, "EUR"), 14)


def test_amortisation_closes_at_zero_with_no_stray_minor_unit() -> None:
    rows = debt.amortisation_schedule(
        Money.of(10_000_003, "USD"),  # deliberately not divisible by 3
        first_payment=date(2026, 3, 31),
        periods=3,
        rate_bps=658,
        calendar=CAL,
    )
    assert len(rows) == 3
    assert rows[-1].closing_balance == Money.of(0, "USD")
    assert Money.sum((row.principal for row in rows), "USD") == Money.of(10_000_003, "USD")
    # Month-end anchors stay on month-end.
    assert [row.due_date for row in rows] == [
        date(2026, 3, 31),
        date(2026, 6, 30),
        date(2026, 9, 30),
    ]
    # No interest accrues on the first instalment; it accrues between dates.
    assert rows[0].interest == Money.of(0, "USD")
    assert rows[1].interest.amount > 0
    assert rows[1].total == rows[1].principal + rows[1].interest


def test_amortisation_frequencies_and_guards() -> None:
    monthly = debt.amortisation_schedule(
        usd(1_200_000),
        first_payment=date(2026, 1, 15),
        periods=12,
        rate_bps=500,
        calendar=CAL,
        frequency=ServiceFrequency.MONTHLY,
    )
    assert len(monthly) == 12
    annual = debt.amortisation_schedule(
        usd(1_200_000),
        first_payment=date(2026, 1, 15),
        periods=2,
        rate_bps=500,
        calendar=CAL,
        frequency=ServiceFrequency.ANNUAL,
    )
    assert annual[1].due_date == date(2027, 1, 15)
    semi = debt.amortisation_schedule(
        usd(1_200_000),
        first_payment=date(2026, 1, 15),
        periods=2,
        rate_bps=500,
        calendar=CAL,
        frequency=ServiceFrequency.SEMIANNUAL,
    )
    assert semi[1].due_date == date(2026, 7, 15)

    with pytest.raises(PolicyError, match="at least one period"):
        debt.amortisation_schedule(
            usd(1), first_payment=date(2026, 1, 15), periods=0, rate_bps=1, calendar=CAL
        )
    with pytest.raises(PolicyError, match="must be non-negative"):
        debt.amortisation_schedule(
            usd(-1), first_payment=date(2026, 1, 15), periods=1, rate_bps=1, calendar=CAL
        )


def test_total_capacity_across_facilities() -> None:
    term = revolver(
        facility_id="term-1",
        name="Term Loan A",
        limit=usd(20_000_000),
        drawn=usd(20_000_000),
        is_revolver=False,
    )
    undrawn, safe = debt.total_capacity((revolver(), term), POLICY)
    assert undrawn == usd(40_000_000)
    assert safe == usd(25_000_000)

    with pytest.raises(PolicyError, match="at least one facility"):
        debt.total_capacity((), POLICY)
    with pytest.raises(PolicyError, match="multiple currencies"):
        debt.total_capacity(
            (revolver(), revolver(limit=Money.of(1, "EUR"), drawn=Money.of(0, "EUR"))), POLICY
        )
