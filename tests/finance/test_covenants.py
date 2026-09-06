"""Covenant tests — 100% branch coverage, per `PHASES.md`.

The covenant thresholds and definitions here follow the conventions found in
real Ex-10 credit agreements (`DATA_SOURCES.md` §3): leverage tested quarterly on
trailing-twelve-month adjusted EBITDA, coverage as a floor, and a minimum
liquidity covenant stated in currency.
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.finance import covenants
from backend.finance.covenants import (
    CovenantDefinition,
    CovenantInputs,
    CovenantKind,
    CovenantTestFrequency,
)
from backend.finance.errors import PolicyError
from backend.finance.money import Money

AS_OF = date(2026, 9, 7)
TEST_DATE = date(2026, 9, 30)


def usd(major: int) -> Money:
    return Money.of(major * 100, "USD")


def inputs(**overrides: Money) -> CovenantInputs:
    defaults: dict[str, Money] = {
        "unrestricted_cash": usd(20_000_000),
        "total_debt": usd(60_000_000),
        "undrawn_revolver": usd(15_000_000),
        "ttm_adjusted_ebitda": usd(20_000_000),
        "ttm_interest_expense": usd(4_000_000),
    }
    defaults.update(overrides)
    return CovenantInputs(**defaults)  # type: ignore[arg-type]


LEVERAGE = CovenantDefinition(
    name="Consolidated Leverage Ratio",
    kind=CovenantKind.NET_DEBT_TO_EBITDA,
    definition=(
        "Consolidated Total Net Debt to Consolidated Adjusted EBITDA for the trailing "
        "four fiscal quarters, tested on the last day of each fiscal quarter"
    ),
    next_test_date=TEST_DATE,
    threshold_bps=35_000,  # 3.50x
)

COVERAGE = CovenantDefinition(
    name="Interest Coverage Ratio",
    kind=CovenantKind.INTEREST_COVERAGE,
    definition="Consolidated Adjusted EBITDA to Consolidated Interest Expense, TTM",
    next_test_date=TEST_DATE,
    threshold_bps=30_000,  # 3.00x
)

MIN_LIQUIDITY = CovenantDefinition(
    name="Minimum Liquidity",
    kind=CovenantKind.MIN_LIQUIDITY,
    definition="Unrestricted cash plus undrawn revolving commitments",
    next_test_date=TEST_DATE,
    threshold_amount=usd(25_000_000),
)

MIN_CASH = CovenantDefinition(
    name="Minimum Cash",
    kind=CovenantKind.MIN_CASH,
    definition="Unrestricted cash held with the Administrative Agent",
    next_test_date=TEST_DATE,
    threshold_amount=usd(10_000_000),
)


def test_derived_quantities() -> None:
    facts = inputs()
    assert facts.net_debt == usd(40_000_000)
    assert facts.liquidity == usd(35_000_000)
    assert facts.currency == "USD"


def test_leverage_passes_with_headroom_stated_both_ways() -> None:
    status = covenants.evaluate(LEVERAGE, inputs(), AS_OF)
    # Net debt $40M / EBITDA $20M = 2.00x = 20000 bps against a 3.50x ceiling.
    assert status.current_value_bps == 20_000
    assert status.threshold_bps == 35_000
    assert status.headroom_bps == 15_000
    # Headroom in currency: 3.50 × $20M = $70M of net debt allowed, less $40M.
    assert status.headroom.amount == usd(30_000_000).amount
    assert status.passed
    assert status.days_to_test == 23
    assert status.definition == LEVERAGE.definition


def test_leverage_breaches_when_net_debt_climbs() -> None:
    status = covenants.evaluate(LEVERAGE, inputs(total_debt=usd(100_000_000)), AS_OF)
    # Net debt $80M / $20M = 4.00x against a 3.50x ceiling.
    assert status.current_value_bps == 40_000
    assert status.headroom_bps == -5_000
    assert status.headroom.amount == usd(-10_000_000).amount
    assert not status.passed


def test_coverage_is_a_floor_not_a_ceiling() -> None:
    status = covenants.evaluate(COVERAGE, inputs(), AS_OF)
    # EBITDA $20M / interest $4M = 5.00x against a 3.00x floor.
    assert status.current_value_bps == 50_000
    assert status.headroom_bps == 20_000
    # Headroom in currency: EBITDA above the $12M the ratio demands.
    assert status.headroom.amount == usd(8_000_000).amount
    assert status.passed

    tight = covenants.evaluate(COVERAGE, inputs(ttm_adjusted_ebitda=usd(10_000_000)), AS_OF)
    assert tight.current_value_bps == 25_000
    assert not tight.passed


def test_amount_covenants() -> None:
    liquidity = covenants.evaluate(MIN_LIQUIDITY, inputs(), AS_OF)
    assert liquidity.headroom.amount == usd(10_000_000).amount
    assert liquidity.current_value_bps is None
    assert liquidity.passed

    cash = covenants.evaluate(MIN_CASH, inputs(unrestricted_cash=usd(9_000_000)), AS_OF)
    assert cash.headroom.amount == usd(-1_000_000).amount
    assert not cash.passed


def test_a_ratio_that_cannot_be_computed_is_a_finding_not_a_crash() -> None:
    """Zero or negative EBITDA is neither a pass nor a divide-by-zero."""
    status = covenants.evaluate(LEVERAGE, inputs(ttm_adjusted_ebitda=usd(0)), AS_OF)
    assert not status.passed
    assert status.current_value_bps is None
    assert status.headroom is None
    assert "not computable" in status.definition
    assert "EBITDA is not positive" in status.definition

    no_interest = covenants.evaluate(COVERAGE, inputs(ttm_interest_expense=usd(0)), AS_OF)
    assert not no_interest.passed
    assert "interest expense is not positive" in no_interest.definition


def test_a_definition_must_carry_the_threshold_its_kind_needs() -> None:
    with pytest.raises(PolicyError, match="ratio covenant needs threshold_bps"):
        CovenantDefinition(
            name="bad",
            kind=CovenantKind.NET_DEBT_TO_EBITDA,
            definition="x",
            next_test_date=TEST_DATE,
        )
    with pytest.raises(PolicyError, match="amount covenant needs threshold_amount"):
        CovenantDefinition(
            name="bad",
            kind=CovenantKind.MIN_CASH,
            definition="x",
            next_test_date=TEST_DATE,
        )


def test_amount_covenant_rejects_a_currency_mismatch() -> None:
    euro = CovenantDefinition(
        name="Minimum Cash (EUR)",
        kind=CovenantKind.MIN_CASH,
        definition="x",
        next_test_date=TEST_DATE,
        threshold_amount=Money.of(100, "EUR"),
    )
    with pytest.raises(PolicyError, match="threshold currency"):
        covenants.evaluate(euro, inputs(), AS_OF)


def test_evaluate_all_orders_by_test_date() -> None:
    earlier = CovenantDefinition(
        name="Minimum Liquidity",
        kind=CovenantKind.MIN_LIQUIDITY,
        definition="x",
        next_test_date=date(2026, 9, 15),
        threshold_amount=usd(25_000_000),
        test_frequency=CovenantTestFrequency.MONTHLY,
    )
    statuses = covenants.evaluate_all((LEVERAGE, COVERAGE, earlier), inputs(), AS_OF)
    assert [status.test_date for status in statuses] == [
        date(2026, 9, 15),
        TEST_DATE,
        TEST_DATE,
    ]
    assert statuses[1].name == "Consolidated Leverage Ratio"


def test_only_covenants_tested_inside_the_horizon_bind() -> None:
    """A covenant not tested in the horizon cannot be breached by the horizon."""
    far = CovenantDefinition(
        name="Annual Leverage",
        kind=CovenantKind.NET_DEBT_TO_EBITDA,
        definition="x",
        next_test_date=date(2027, 6, 30),
        threshold_bps=35_000,
        test_frequency=CovenantTestFrequency.ANNUAL,
    )
    horizon_end = date(2026, 12, 6)
    assert covenants.tested_within((LEVERAGE, far), AS_OF, horizon_end) == (LEVERAGE,)
    assert covenants.tested_within((far,), AS_OF, horizon_end) == ()


def test_binding_headroom_picks_the_tightest_covenant() -> None:
    statuses = covenants.evaluate_all((LEVERAGE, COVERAGE, MIN_LIQUIDITY), inputs(), AS_OF)
    binding = covenants.binding_headroom(statuses)
    assert binding is not None
    # Coverage headroom $8M is tighter than liquidity $10M and leverage $30M.
    assert binding.name == "Interest Coverage Ratio"


def test_binding_headroom_returns_none_when_nothing_is_computable() -> None:
    statuses = covenants.evaluate_all((LEVERAGE,), inputs(ttm_adjusted_ebitda=usd(0)), AS_OF)
    assert covenants.binding_headroom(statuses) is None
