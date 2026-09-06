"""Unit tests for Money — exact arithmetic, no float."""

from __future__ import annotations

import pytest

from backend.finance import CurrencyMismatch, FXRate, Money, MoneyError


def test_from_major_usd() -> None:
    assert Money.from_major("1234.56", "USD") == Money.of(123456, "USD")
    assert Money.from_major("-0.07", "USD") == Money.of(-7, "USD")
    assert Money.from_major("15", "USD") == Money.of(1500, "USD")


def test_from_major_jpy_zero_exponent() -> None:
    assert Money.from_major("1500", "JPY") == Money.of(1500, "JPY")
    with pytest.raises(MoneyError):
        Money.from_major("1500.5", "JPY")


def test_from_major_bhd_three_decimals() -> None:
    assert Money.from_major("1.005", "BHD") == Money.of(1005, "BHD")


def test_from_major_rejects_float_precision_trap() -> None:
    with pytest.raises(MoneyError):
        Money.from_major("1.234", "USD")


def test_from_major_rejects_empty_and_junk() -> None:
    with pytest.raises(MoneyError):
        Money.from_major("", "USD")
    with pytest.raises(MoneyError):
        Money.from_major("12.3.4", "USD")
    with pytest.raises(MoneyError):
        Money.from_major("abc", "USD")


def test_rejects_non_int_amount() -> None:
    with pytest.raises(MoneyError):
        Money(1.5, "USD")  # type: ignore[arg-type]
    with pytest.raises(MoneyError):
        Money(True, "USD")  # type: ignore[arg-type]


def test_unknown_currency_fails_closed() -> None:
    with pytest.raises(MoneyError):
        Money.of(1, "XXX")


def test_add_subtract_same_currency() -> None:
    a = Money.from_major("10.00", "USD")
    b = Money.from_major("2.50", "USD")
    assert a + b == Money.from_major("12.50", "USD")
    assert a - b == Money.from_major("7.50", "USD")
    assert a + (-b) == a - b
    assert abs(Money.of(-5, "USD")) == Money.of(5, "USD")


def test_add_rejects_currency_mismatch() -> None:
    with pytest.raises(CurrencyMismatch):
        _ = Money.of(1, "USD") + Money.of(1, "EUR")


def test_multiply_int_only() -> None:
    assert Money.of(250, "USD") * 4 == Money.of(1000, "USD")
    assert 3 * Money.of(100, "USD") == Money.of(300, "USD")
    with pytest.raises(MoneyError):
        _ = Money.of(100, "USD") * 1.5  # type: ignore[operator]


def test_sum() -> None:
    items = [Money.of(100, "USD"), Money.of(50, "USD"), Money.of(-20, "USD")]
    assert Money.sum(items, "USD") == Money.of(130, "USD")
    assert Money.sum([], "USD") == Money.zero("USD")
    with pytest.raises(CurrencyMismatch):
        Money.sum([Money.of(1, "USD"), Money.of(1, "EUR")], "USD")


def test_allocate_no_leakage() -> None:
    parts = Money.of(100, "USD").allocate([1, 1, 1])
    assert sum(p.amount for p in parts) == 100
    assert {p.amount for p in parts} == {34, 33}
    assert parts[0].amount == 34
    assert parts[1].amount == 33
    assert parts[2].amount == 33


def test_allocate_uneven_ratios() -> None:
    parts = Money.from_major("10.00", "USD").allocate([1, 2, 1])
    assert Money.sum(parts, "USD") == Money.from_major("10.00", "USD")
    assert parts[1] > parts[0]


def test_allocate_negative_amount_preserves_sign() -> None:
    parts = Money.of(-100, "USD").split(3)
    assert Money.sum(parts, "USD") == Money.of(-100, "USD")
    assert all(p.amount <= 0 for p in parts)


def test_allocate_rejects_bad_ratios() -> None:
    money = Money.of(100, "USD")
    with pytest.raises(MoneyError):
        money.allocate([])
    with pytest.raises(MoneyError):
        money.allocate([0, 0])
    with pytest.raises(MoneyError):
        money.allocate([-1, 2])


def test_to_major_string_round_trip() -> None:
    original = Money.from_major("-1234.50", "USD")
    assert Money.from_major(original.to_major_string(), "USD") == original
    jpy = Money.from_major("1500", "JPY")
    assert Money.from_major(jpy.to_major_string(), "JPY") == jpy


def test_convert_identity() -> None:
    money = Money.from_major("20.00", "USD")
    rate = FXRate.identity("USD")
    converted, leftover = money.convert_with_remainder("USD", rate)
    assert converted == money
    assert leftover == 0


def test_convert_eur_usd() -> None:
    # 1 EUR = 1.10 USD
    rate = FXRate.from_decimal_string("EUR", "USD", "1.10")
    euros = Money.from_major("10.00", "EUR")
    dollars = euros.convert("USD", rate)
    assert dollars.currency.code == "USD"
    assert dollars == Money.from_major("11.00", "USD")


def test_convert_inverse_rate() -> None:
    rate = FXRate.from_decimal_string("EUR", "USD", "1.10")
    dollars = Money.from_major("11.00", "USD")
    euros = dollars.convert("EUR", rate)
    assert euros == Money.from_major("10.00", "EUR")


def test_convert_rejects_wrong_pair() -> None:
    rate = FXRate.from_decimal_string("EUR", "USD", "1.10")
    with pytest.raises(MoneyError):
        Money.of(100, "GBP").convert("JPY", rate)


def test_fx_rate_rejects_non_positive() -> None:
    with pytest.raises(MoneyError):
        FXRate.from_decimal_string("USD", "EUR", "0")
    with pytest.raises(MoneyError):
        FXRate.from_decimal_string("USD", "EUR", "-1.2")
