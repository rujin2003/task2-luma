"""Integer-only rounding. Half-even (banker's) is the default for conversion."""

from __future__ import annotations

from enum import StrEnum


class Rounding(StrEnum):
    HALF_EVEN = "half_even"
    HALF_UP = "half_up"
    DOWN = "down"


def div_round(n: int, d: int, mode: Rounding = Rounding.HALF_EVEN) -> int:
    """Divide integers `n / d` with an explicit rounding mode.

    Works for negative numerators and denominators. Never uses float.
    """
    if d == 0:
        raise ZeroDivisionError("division by zero")

    sign = 1
    if n < 0:
        sign = -sign
        n = -n
    if d < 0:
        sign = -sign
        d = -d

    q, r = divmod(n, d)
    if r == 0:
        return sign * q

    if mode is Rounding.DOWN:
        return sign * q

    two_r = r * 2
    if mode is Rounding.HALF_UP:
        if two_r >= d:
            q += 1
        return sign * q

    # HALF_EVEN: exactly halfway goes to even quotient.
    if two_r > d or (two_r == d and q % 2 == 1):
        q += 1
    return sign * q
