from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.finance import Money, PolicyError, TreasuryPolicy
from backend.finance.policy import DEFAULT_POLICY_PATH, MoneyLiteral


def test_default_policy_loads() -> None:
    policy = TreasuryPolicy.load()
    assert policy.version == 1
    assert policy.currency == "USD"
    assert policy.min_unrestricted_cash.to_money() == Money.from_major("15000000.00", "USD")
    assert policy.min_30d_liquidity.to_money() == Money.from_major("20000000.00", "USD")
    assert policy.max_revolver_utilization_bps == 7000
    assert policy.is_protected("payroll")
    assert policy.is_protected("TAX")
    assert policy.is_protected("statutory")
    assert not policy.is_protected("rent")
    assert DEFAULT_POLICY_PATH.exists()


def test_materiality_resolves_to_lesser_of_floor_and_percent() -> None:
    policy = TreasuryPolicy.load()
    # 1% of $1,000,000 opex = $10,000; floor is $50,000 → $10,000 wins
    cheap = policy.materiality.resolve(Money.from_major("1000000.00", "USD"))
    assert cheap == Money.from_major("10000.00", "USD")
    # 1% of $20,000,000 opex = $200,000; floor is $50,000 → floor wins
    expensive = policy.materiality.resolve(Money.from_major("20000000.00", "USD"))
    assert expensive == Money.from_major("50000.00", "USD")


def test_rejects_missing_payroll_protection() -> None:
    with pytest.raises(PolicyError):
        TreasuryPolicy.from_mapping(
            {
                "version": 1,
                "effective_at": datetime(2026, 1, 1, tzinfo=UTC),
                "currency": "USD",
                "min_unrestricted_cash": {"amount": 1, "currency": "USD"},
                "min_30d_liquidity": {"amount": 2, "currency": "USD"},
                "max_revolver_utilization_bps": 7000,
                "protected_payment_classes": ["statutory"],
                "materiality": {
                    "absolute": {"amount": 1, "currency": "USD"},
                    "pct_of_monthly_opex_bps": 100,
                },
            }
        )


def test_rejects_mixed_currency() -> None:
    with pytest.raises(PolicyError):
        TreasuryPolicy.from_mapping(
            {
                "version": 1,
                "effective_at": datetime(2026, 1, 1, tzinfo=UTC),
                "currency": "USD",
                "min_unrestricted_cash": {"amount": 1, "currency": "EUR"},
                "min_30d_liquidity": {"amount": 2, "currency": "USD"},
                "max_revolver_utilization_bps": 7000,
                "protected_payment_classes": ["payroll", "tax"],
                "materiality": {
                    "absolute": {"amount": 1, "currency": "USD"},
                    "pct_of_monthly_opex_bps": 100,
                },
            }
        )


def test_money_literal_round_trip() -> None:
    literal = MoneyLiteral(amount=1500, currency="usd")
    assert literal.currency == "USD"
    assert literal.to_money() == Money.of(1500, "USD")
