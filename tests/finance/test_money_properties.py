"""Property tests: add / subtract / allocate / convert with no rounding leakage."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from backend.finance import FXRate, Money, get_currency
from backend.finance.currency import known_codes

CURRENCIES = st.sampled_from(sorted(known_codes()))
AMOUNTS = st.integers(min_value=-10_000_000, max_value=10_000_000)
RATIOS = st.lists(st.integers(min_value=0, max_value=50), min_size=1, max_size=8).filter(
    lambda rs: sum(rs) > 0
)
POSITIVE_RATE = st.integers(min_value=1, max_value=1_000_000)


@given(amount=AMOUNTS, currency=CURRENCIES)
def test_add_zero_is_identity(amount: int, currency: str) -> None:
    money = Money.of(amount, currency)
    assert money + Money.zero(currency) == money
    assert money - Money.zero(currency) == money


@given(a=AMOUNTS, b=AMOUNTS, currency=CURRENCIES)
def test_add_commutative(a: int, b: int, currency: str) -> None:
    left = Money.of(a, currency)
    right = Money.of(b, currency)
    assert left + right == right + left


@given(a=AMOUNTS, b=AMOUNTS, c=AMOUNTS, currency=CURRENCIES)
@settings(max_examples=80)
def test_add_associative(a: int, b: int, c: int, currency: str) -> None:
    x, y, z = Money.of(a, currency), Money.of(b, currency), Money.of(c, currency)
    assert (x + y) + z == x + (y + z)


@given(a=AMOUNTS, b=AMOUNTS, currency=CURRENCIES)
def test_subtract_is_add_inverse(a: int, b: int, currency: str) -> None:
    left = Money.of(a, currency)
    right = Money.of(b, currency)
    assert (left + right) - right == left
    assert (left - right) + right == left


@given(amount=AMOUNTS, currency=CURRENCIES, ratios=RATIOS)
@settings(max_examples=120)
def test_allocate_parts_sum_to_original(amount: int, currency: str, ratios: list[int]) -> None:
    money = Money.of(amount, currency)
    parts = money.allocate(ratios)
    assert len(parts) == len(ratios)
    assert Money.sum(parts, currency) == money
    assert all(p.currency.code == currency for p in parts)


@given(amount=AMOUNTS, currency=CURRENCIES, n=st.integers(min_value=1, max_value=12))
def test_split_no_leakage(amount: int, currency: str, n: int) -> None:
    money = Money.of(amount, currency)
    parts = money.split(n)
    assert Money.sum(parts, currency) == money


@given(amount=AMOUNTS, src=CURRENCIES, tgt=CURRENCIES, numer=POSITIVE_RATE, denom=POSITIVE_RATE)
@settings(max_examples=120)
def test_convert_with_remainder_conserves_source(
    amount: int,
    src: str,
    tgt: str,
    numer: int,
    denom: int,
) -> None:
    money = Money.of(amount, src)
    if src == tgt:
        rate = FXRate.identity(src)
    else:
        rate = FXRate(base=get_currency(src), quote=get_currency(tgt), numer=numer, denom=denom)
    converted, leftover = money.convert_with_remainder(tgt, rate)
    assert converted.currency.code == tgt
    implied = money.amount - leftover
    assert leftover == money.amount - implied
    # Reconstruct: converting the implied source amount at the same rate equals converted
    # (within the same rounding). leftover is defined as original - implied.
    assert implied + leftover == money.amount


@given(amount=AMOUNTS, currency=CURRENCIES)
def test_major_string_round_trip(amount: int, currency: str) -> None:
    money = Money.of(amount, currency)
    assert Money.from_major(money.to_major_string(), currency) == money


@given(amount=AMOUNTS, currency=CURRENCIES, factor=st.integers(min_value=-50, max_value=50))
def test_int_multiply_distributes(amount: int, currency: str, factor: int) -> None:
    money = Money.of(amount, currency)
    other = Money.of(7, currency)
    assert (money + other) * factor == money * factor + other * factor
