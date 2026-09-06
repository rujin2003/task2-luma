from __future__ import annotations

from backend.contracts.common import FrozenModel, MoneyDTO


class DodoMetrics(FrozenModel):
    expected_collections: MoneyDTO
    at_risk: MoneyDTO
    recoverable: MoneyDTO
    success_rate_bps: int
    success_rate_delta_bps: int
    payout_lag_days: int
    dispute_reserve: MoneyDTO
    degraded: bool = False
    degraded_reason: str | None = None
