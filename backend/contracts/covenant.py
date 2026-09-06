from __future__ import annotations

from datetime import date

from backend.contracts.common import FrozenModel, MoneyDTO


class CovenantStatus(FrozenModel):
    """A covenant evaluated on its real test date and definition."""

    name: str
    definition: str
    test_date: date
    current_value_bps: int | None = None
    threshold_bps: int | None = None
    headroom: MoneyDTO | None = None
    headroom_bps: int | None = None
    passed: bool
    days_to_test: int
    currency: str
