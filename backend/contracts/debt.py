from __future__ import annotations

from backend.contracts.common import FrozenModel, MoneyDTO


class DebtCapacity(FrozenModel):
    facility_id: str
    limit: MoneyDTO
    drawn: MoneyDTO
    undrawn: MoneyDTO
    utilization_bps: int
    all_in_draw_cost_bps: int
    max_safe_draw: MoneyDTO
    commitment_fee_bps: int = 0
