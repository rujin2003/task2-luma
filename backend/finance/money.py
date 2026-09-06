"""Money: integer minor units + explicit ISO-4217 currency.

No float is used anywhere in this module. Parsing of major-unit strings is
digit-splitting. Conversion uses integer ratios. Allocation uses the largest-
remainder method so parts always sum to the original amount.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Self

from backend.finance.currency import Currency, get_currency
from backend.finance.errors import CurrencyMismatch, MoneyError
from backend.finance.fx import FXRate
from backend.finance.rounding import Rounding, div_round


@dataclass(frozen=True, slots=True)
class Money:
    """A signed monetary amount in minor units of a single currency."""

    amount: int
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.amount, int) or isinstance(self.amount, bool):
            raise MoneyError(f"amount must be int minor units, got {type(self.amount).__name__}")
        object.__setattr__(self, "currency", get_currency(self.currency))

    @classmethod
    def zero(cls, currency: Currency | str) -> Self:
        return cls(0, get_currency(currency))

    @classmethod
    def of(cls, amount: int, currency: Currency | str) -> Self:
        return cls(amount, get_currency(currency))

    @classmethod
    def from_major(cls, text: str, currency: Currency | str) -> Self:
        """Parse a major-unit decimal string ('-1234.56') into minor units."""
        ccy = get_currency(currency)
        raw = text.strip()
        if not raw:
            raise MoneyError("empty major-unit string")
        sign = 1
        if raw[0] == "+":
            raw = raw[1:]
        elif raw[0] == "-":
            sign = -1
            raw = raw[1:]
        if not raw or raw.count(".") > 1:
            raise MoneyError(f"invalid major-unit string: {text!r}")
        if "." in raw:
            whole, frac = raw.split(".", 1)
        else:
            whole, frac = raw, ""
        if whole == "":
            whole = "0"
        if not whole.isdigit() or (frac and not frac.isdigit()):
            raise MoneyError(f"invalid major-unit string: {text!r}")
        if len(frac) > ccy.exponent:
            raise MoneyError(f"{text!r} has more than {ccy.exponent} decimal places for {ccy.code}")
        frac = frac.ljust(ccy.exponent, "0")
        minor = int(whole) * ccy.minor_units + (int(frac) if frac else 0)
        return cls(sign * minor, ccy)

    def to_major_string(self) -> str:
        """Render major units without using float."""
        sign = "-" if self.amount < 0 else ""
        abs_amount = abs(self.amount)
        scale = self.currency.minor_units
        if scale == 1:
            return f"{sign}{abs_amount}"
        whole, frac = divmod(abs_amount, scale)
        width = self.currency.exponent
        return f"{sign}{whole}.{frac:0{width}d}"

    def _same_currency(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise MoneyError(f"expected Money, got {type(other).__name__}")
        if self.currency.code != other.currency.code:
            raise CurrencyMismatch(
                f"cannot combine {self.currency.code} with {other.currency.code}"
            )

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __abs__(self) -> Money:
        return Money(abs(self.amount), self.currency)

    def __mul__(self, factor: int) -> Money:
        if not isinstance(factor, int) or isinstance(factor, bool):
            raise MoneyError("Money can only be multiplied by int")
        return Money(self.amount * factor, self.currency)

    def __rmul__(self, factor: int) -> Money:
        return self.__mul__(factor)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.amount == other.amount and self.currency.code == other.currency.code

    def __lt__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount >= other.amount

    def __hash__(self) -> int:
        return hash((self.amount, self.currency.code))

    def __repr__(self) -> str:
        return f"Money({self.amount}, {self.currency.code!r})"

    def __str__(self) -> str:
        return f"{self.to_major_string()} {self.currency.code}"

    @classmethod
    def sum(cls, items: Iterable[Money], currency: Currency | str) -> Money:
        total = cls.zero(currency)
        for item in items:
            total = total + item
        return total

    def allocate(self, ratios: Sequence[int]) -> tuple[Money, ...]:
        """Split this amount by `ratios` with no remainder leakage.

        Largest-remainder (Hamilton) method: floors take their share, leftover
        minor units go to the largest remainders. Parts always sum to `self`.
        """
        if not ratios:
            raise MoneyError("allocate requires at least one ratio")
        if any(not isinstance(r, int) or isinstance(r, bool) or r < 0 for r in ratios):
            raise MoneyError("allocate ratios must be non-negative ints")
        total_ratio = sum(ratios)
        if total_ratio == 0:
            raise MoneyError("allocate ratios must sum to a positive integer")

        sign = 1 if self.amount >= 0 else -1
        amount = abs(self.amount)
        floors: list[int] = []
        remainders: list[int] = []
        for ratio in ratios:
            product = amount * ratio
            floors.append(product // total_ratio)
            remainders.append(product % total_ratio)
        leftover = amount - sum(floors)
        order = sorted(range(len(ratios)), key=lambda i: (-remainders[i], i))
        for index in order[:leftover]:
            floors[index] += 1
        return tuple(Money(sign * part, self.currency) for part in floors)

    def split(self, parts: int) -> tuple[Money, ...]:
        if parts <= 0:
            raise MoneyError("split requires a positive number of parts")
        return self.allocate([1] * parts)

    def convert(
        self,
        target: Currency | str,
        rate: FXRate,
        *,
        rounding: Rounding = Rounding.HALF_EVEN,
    ) -> Money:
        """Convert using `rate` (quote per base, major units).

        Formula (all integers):
            target_minor = src_minor * tgt_scale * numer / (src_scale * denom)

        A single conversion may round by at most one minor unit of the target.
        Use `convert_with_remainder` when conservation of the source amount
        must be proven.
        """
        converted, _remainder = self.convert_with_remainder(target, rate, rounding=rounding)
        return converted

    def convert_with_remainder(
        self,
        target: Currency | str,
        rate: FXRate,
        *,
        rounding: Rounding = Rounding.HALF_EVEN,
    ) -> tuple[Money, int]:
        """Return `(converted, leftover_source_minor)` after integer conversion.

        `leftover` is the source-minor residual that did not survive rounding,
        reconstructed so that converting the leftover at the same rate would
        produce a zero target amount under the same rounding. The converted
        amount plus the residual reconstructed via the inverse integer formula
        accounts for the original source amount.
        """
        tgt = get_currency(target)
        src = self.currency
        if src.code == tgt.code:
            if rate.base.code != src.code or rate.quote.code != tgt.code:
                raise MoneyError("same-currency convert requires an identity-pair rate")
            return Money(self.amount, src), 0

        if rate.base.code == src.code and rate.quote.code == tgt.code:
            numer, denom = rate.numer, rate.denom
        elif rate.base.code == tgt.code and rate.quote.code == src.code:
            numer, denom = rate.denom, rate.numer
        else:
            raise MoneyError(
                f"rate {rate.base.code}/{rate.quote.code} cannot convert {src.code} -> {tgt.code}"
            )

        # target_minor = src_minor * tgt.minor_units * numer / (src.minor_units * denom)
        n = self.amount * tgt.minor_units * numer
        d = src.minor_units * denom
        converted_minor = div_round(n, d, rounding)
        # Reconstruct the source-minor implied by the rounded target, then leftover.
        back_n = converted_minor * src.minor_units * denom
        back_d = tgt.minor_units * numer
        implied_source = div_round(back_n, back_d, rounding)
        leftover = self.amount - implied_source
        return Money(converted_minor, tgt), leftover
