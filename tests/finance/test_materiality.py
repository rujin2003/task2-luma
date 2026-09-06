"""Materiality resolution — the lesser of a floor and a % of monthly opex."""

from __future__ import annotations

import pytest

from backend.finance import materiality
from backend.finance.errors import PolicyError
from backend.finance.money import Money
from backend.finance.policy import TreasuryPolicy

POLICY = TreasuryPolicy.load()


def usd(major: int) -> Money:
    return Money.of(major * 100, "USD")


def test_the_percentage_limb_binds_for_a_small_company() -> None:
    # 100 bps of $2,000,000 monthly opex is $20,000, below the $50,000 floor.
    threshold = materiality.resolve_threshold(POLICY, usd(2_000_000))
    assert threshold == usd(20_000)
    assert "bps of monthly opex" in materiality.threshold_basis(POLICY, usd(2_000_000))


def test_the_absolute_floor_binds_for_a_large_company() -> None:
    # 100 bps of $12,000,000 monthly opex is $120,000, above the $50,000 floor.
    threshold = materiality.resolve_threshold(POLICY, usd(12_000_000))
    assert threshold == usd(50_000)
    assert "absolute floor" in materiality.threshold_basis(POLICY, usd(12_000_000))


def test_assess_ignores_sign_and_reports_its_basis() -> None:
    opex = usd(12_000_000)
    inflow = materiality.assess(POLICY, opex, usd(60_000))
    outflow = materiality.assess(POLICY, opex, usd(-60_000))
    assert inflow.material and outflow.material
    assert "is material against" in str(inflow)

    small = materiality.assess(POLICY, opex, usd(4_000))
    assert not small.material
    assert "is immaterial against" in str(small)


def test_the_threshold_itself_is_material() -> None:
    """`>=` not `>`: an item exactly at threshold is explained, not swallowed."""
    assert materiality.is_material(POLICY, usd(12_000_000), usd(50_000))
    assert not materiality.is_material(POLICY, usd(12_000_000), usd(49_999))


def test_currency_mismatches_are_rejected_rather_than_coerced() -> None:
    with pytest.raises(PolicyError):
        materiality.resolve_threshold(POLICY, Money.of(1, "EUR"))
    with pytest.raises(PolicyError, match="materiality threshold"):
        materiality.assess(POLICY, usd(12_000_000), Money.of(1, "EUR"))
