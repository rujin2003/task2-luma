"""FX rates as integer ratios. Currency is never inferred at runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.finance.currency import Currency, get_currency
from backend.finance.errors import MoneyError


@dataclass(frozen=True, slots=True)
class FXRate:
    """Quote/base major-unit ratio stored as `numer / denom`.

    `numer / denom` is the number of *major* units of `quote` per one *major*
    unit of `base`. Both integers are required so conversion stays exact until
    the final minor-unit rounding step.

    Example: EURUSD = 1.0850 is FXRate(base=EUR, quote=USD, numer=10850, denom=10000).
    """

    base: Currency
    quote: Currency
    numer: int
    denom: int
    as_of: datetime | None = None
    source: str = "manual"

    def __post_init__(self) -> None:
        object.__setattr__(self, "base", get_currency(self.base))
        object.__setattr__(self, "quote", get_currency(self.quote))
        if self.numer <= 0 or self.denom <= 0:
            raise MoneyError("FX rate numer and denom must be positive integers")
        if self.base.code == self.quote.code and self.numer != self.denom:
            raise MoneyError("same-currency rate must be 1")

    def invert(self) -> FXRate:
        return FXRate(
            base=self.quote,
            quote=self.base,
            numer=self.denom,
            denom=self.numer,
            as_of=self.as_of,
            source=self.source,
        )

    @classmethod
    def identity(cls, currency: Currency | str, *, as_of: datetime | None = None) -> FXRate:
        ccy = get_currency(currency)
        return cls(base=ccy, quote=ccy, numer=1, denom=1, as_of=as_of, source="identity")

    @classmethod
    def from_decimal_string(
        cls,
        base: Currency | str,
        quote: Currency | str,
        rate: str,
        *,
        as_of: datetime | None = None,
        source: str = "manual",
    ) -> FXRate:
        """Parse a decimal rate string such as '1.0850' into an integer ratio."""
        text = rate.strip()
        if not text or text.startswith("-"):
            raise MoneyError(f"FX rate must be a positive decimal string, got {rate!r}")
        if "." in text:
            whole, frac = text.split(".", 1)
        else:
            whole, frac = text, ""
        if not whole.isdigit() or (frac and not frac.isdigit()):
            raise MoneyError(f"FX rate must be a positive decimal string, got {rate!r}")
        if not frac:
            return cls(
                base=get_currency(base),
                quote=get_currency(quote),
                numer=int(whole),
                denom=1,
                as_of=as_of,
                source=source,
            )
        denom = 10 ** len(frac)
        numer = int(whole) * denom + int(frac)
        if numer == 0:
            raise MoneyError("FX rate must be positive")
        return cls(
            base=get_currency(base),
            quote=get_currency(quote),
            numer=numer,
            denom=denom,
            as_of=as_of,
            source=source,
        )
