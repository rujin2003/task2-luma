from __future__ import annotations

from backend.contracts.common import FrozenModel, MoneyDTO


class Constraint(FrozenModel):
    """Structured pass/fail from the deterministic constraint engine."""

    name: str
    passed: bool
    actual: MoneyDTO | int | None = None
    limit: MoneyDTO | int | None = None
    margin: MoneyDTO | int | None = None
    reason: str
