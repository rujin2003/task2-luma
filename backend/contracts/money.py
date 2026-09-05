"""Exact monetary arithmetic.

Money is integer minor units plus an explicit ISO-4217 currency. There is no float
anywhere in this module and float inputs are rejected rather than coerced -- binary
floating point cannot represent 0.1 exactly, and a treasury system that silently
accepts one has already lost the audit trail.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Minor-unit exponents for the currencies v1 handles. Anything else must be added
# deliberately -- guessing "2" for a zero-decimal currency mis-states amounts 100x.
CURRENCY_EXPONENTS: dict[str, int] = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "CAD": 2,
    "AUD": 2,
    "CHF": 2,
    "SGD": 2,
    "INR": 2,
    "JPY": 0,
    "KRW": 0,
}

CurrencyCode = Annotated[str, Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")]

Numeric = int | str | Decimal


def _to_decimal(value: Numeric, *, what: str) -> Decimal:
    if isinstance(value, float):  # pragma: no cover - defended by type checker too
        raise TypeError(f"{what} must not be a float; pass Decimal, int or str")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{what} is not a valid decimal: {value!r}") from exc


class Money(BaseModel):
    """An exact amount in a single currency."""

    model_config = ConfigDict(frozen=True)

    minor_units: int
    currency: CurrencyCode

    @model_validator(mode="after")
    def _known_currency(self) -> Self:
        if self.currency not in CURRENCY_EXPONENTS:
            raise ValueError(
                f"unknown currency {self.currency!r}; add its minor-unit exponent "
                "to CURRENCY_EXPONENTS before using it"
            )
        return self

    # ---- construction -------------------------------------------------

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(minor_units=0, currency=currency)

    @classmethod
    def from_major(cls, amount: Numeric, currency: str) -> Money:
        """Build from a major-unit amount, e.g. `Money.from_major("12.34", "USD")`.

        Rejects amounts with more precision than the currency can hold rather than
        rounding them away silently.
        """
        exponent = CURRENCY_EXPONENTS.get(currency.upper())
        if exponent is None:
            raise ValueError(f"unknown currency {currency!r}")
        dec = _to_decimal(amount, what="amount")
        scaled = dec.scaleb(exponent)
        if scaled != scaled.to_integral_value():
            raise ValueError(
                f"{dec} has more precision than {currency} supports "
                f"({exponent} minor digits); round explicitly first"
            )
        return cls(minor_units=int(scaled), currency=currency.upper())

    # ---- inspection ---------------------------------------------------

    @property
    def exponent(self) -> int:
        return CURRENCY_EXPONENTS[self.currency]

    def to_major(self) -> Decimal:
        return Decimal(self.minor_units).scaleb(-self.exponent)

    def is_zero(self) -> bool:
        return self.minor_units == 0

    def __str__(self) -> str:
        return f"{self.to_major():.{self.exponent}f} {self.currency}"

    # ---- arithmetic ---------------------------------------------------

    def _same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValueError(
                f"cannot combine {self.currency} and {other.currency}; convert explicitly"
            )

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(minor_units=self.minor_units + other.minor_units, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(minor_units=self.minor_units - other.minor_units, currency=self.currency)

    def __neg__(self) -> Money:
        return Money(minor_units=-self.minor_units, currency=self.currency)

    def __abs__(self) -> Money:
        return Money(minor_units=abs(self.minor_units), currency=self.currency)

    def __lt__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.minor_units < other.minor_units

    def __le__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.minor_units <= other.minor_units

    def __gt__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.minor_units > other.minor_units

    def __ge__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.minor_units >= other.minor_units

    def scale(self, factor: Numeric, *, rounding: str = ROUND_HALF_EVEN) -> Money:
        """Multiply by a dimensionless factor (a probability, a percentage, a count)."""
        dec = _to_decimal(factor, what="factor")
        scaled = (Decimal(self.minor_units) * dec).quantize(Decimal(1), rounding=rounding)
        return Money(minor_units=int(scaled), currency=self.currency)

    def convert(self, rate: Numeric, to_currency: str, *, rounding: str = ROUND_HALF_EVEN) -> Money:
        """Convert using a supplied rate. The system never invents a rate."""
        target = to_currency.upper()
        target_exponent = CURRENCY_EXPONENTS.get(target)
        if target_exponent is None:
            raise ValueError(f"unknown currency {to_currency!r}")
        dec_rate = _to_decimal(rate, what="rate")
        if dec_rate <= 0:
            raise ValueError("fx rate must be positive")
        major = self.to_major() * dec_rate
        scaled = major.scaleb(target_exponent).quantize(Decimal(1), rounding=rounding)
        return Money(minor_units=int(scaled), currency=target)

    def allocate(self, weights: list[int]) -> list[Money]:
        """Split across integer weights with no rounding leakage.

        Largest-remainder distribution: the parts always sum back to the original.
        """
        if not weights:
            raise ValueError("weights must not be empty")
        if any(w < 0 for w in weights):
            raise ValueError("weights must be non-negative")
        total = sum(weights)
        if total == 0:
            raise ValueError("weights must not sum to zero")

        # Work on the magnitude so negative amounts distribute the same way.
        sign = -1 if self.minor_units < 0 else 1
        magnitude = abs(self.minor_units)

        shares = [magnitude * w // total for w in weights]
        remainder = magnitude - sum(shares)
        # Rank by fractional part descending, ties broken by original position.
        order = sorted(
            range(len(weights)),
            key=lambda i: (-(magnitude * weights[i] % total), i),
        )
        for i in order[:remainder]:
            shares[i] += 1
        return [Money(minor_units=sign * s, currency=self.currency) for s in shares]


def money_sum(amounts: list[Money], currency: str) -> Money:
    """Sum a list, with the currency stated explicitly so an empty list still types."""
    total = Money.zero(currency)
    for amount in amounts:
        total = total + amount
    return total


def as_json_scalar(amount: Money) -> dict[str, Any]:
    """Wire form: always the pair, never a bare number."""
    return {"minor_units": amount.minor_units, "currency": amount.currency}
