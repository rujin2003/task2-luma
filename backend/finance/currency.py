"""ISO-4217 currencies with an explicit minor-unit exponent.

Amounts never enter the financial path without a currency. Exponents come from
ISO-4217 (0, 2, or 3 decimal places). Unknown codes are rejected — we do not
infer a default exponent at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.finance.errors import MoneyError

# ISO-4217 alphabetic code → minor-unit exponent (10**exponent = minor units
# per major unit). Subset covering treasury-relevant and conversion-test cases.
_ISO_4217_EXPONENTS: dict[str, int] = {
    "AED": 2,
    "AUD": 2,
    "BHD": 3,
    "BRL": 2,
    "CAD": 2,
    "CHF": 2,
    "CLP": 0,
    "CNY": 2,
    "DKK": 2,
    "EUR": 2,
    "GBP": 2,
    "HKD": 2,
    "INR": 2,
    "JPY": 0,
    "KRW": 0,
    "KWD": 3,
    "MXN": 2,
    "NOK": 2,
    "NZD": 2,
    "OMR": 3,
    "SEK": 2,
    "SGD": 2,
    "USD": 2,
    "ZAR": 2,
}


@dataclass(frozen=True, slots=True)
class Currency:
    """An ISO-4217 currency with a frozen minor-unit scale."""

    code: str
    exponent: int

    def __post_init__(self) -> None:
        if len(self.code) != 3 or not self.code.isalpha() or self.code != self.code.upper():
            raise MoneyError(f"currency code must be ISO-4217 alpha-3, got {self.code!r}")
        if self.exponent not in (0, 2, 3):
            raise MoneyError(f"unsupported ISO-4217 exponent {self.exponent} for {self.code}")

    @property
    def minor_units(self) -> int:
        """Minor units per major unit (1, 100, or 1000)."""
        scale = 1
        for _ in range(self.exponent):
            scale *= 10
        return scale

    def __str__(self) -> str:
        return self.code


def get_currency(code: str | Currency) -> Currency:
    """Resolve a currency code. Unknown codes fail closed."""
    if isinstance(code, Currency):
        return code
    if not isinstance(code, str):
        raise MoneyError(f"currency must be str or Currency, got {type(code).__name__}")
    normalized = code.strip().upper()
    if normalized not in _ISO_4217_EXPONENTS:
        raise MoneyError(f"unknown or unsupported ISO-4217 currency: {code!r}")
    return Currency(normalized, _ISO_4217_EXPONENTS[normalized])


def known_codes() -> frozenset[str]:
    return frozenset(_ISO_4217_EXPONENTS)
