"""Typed failures for the financial path."""


class MoneyError(ValueError):
    """Invalid monetary operation."""


class CurrencyMismatch(MoneyError):
    """Two Money values do not share a currency."""


class PolicyError(ValueError):
    """TreasuryPolicy failed to load or validate."""
