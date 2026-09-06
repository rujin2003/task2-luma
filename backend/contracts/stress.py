from __future__ import annotations

from backend.contracts.common import FrozenModel, MoneyDTO


class StressResult(FrozenModel):
    strategy_id: str
    stressor: str
    passed: bool
    min_cash: MoneyDTO
    threshold: MoneyDTO
    failure_reason: str | None = None
    calibrated_from: str | None = None
