from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from backend.contracts.common import FrozenModel, MoneyDTO
from backend.finance.provenance import Provenance


class ForecastLineDTO(FrozenModel):
    category: str
    week_start: date
    week_end: date
    amount: MoneyDTO
    source: str
    assumption: str
    method: str
    as_of: datetime
    provenance: Provenance


class ForecastGrid(FrozenModel):
    version_id: str
    as_of: datetime
    opening_cash: MoneyDTO
    weeks: tuple[date, ...]
    lines: tuple[ForecastLineDTO, ...]
    closing_cash_by_week: tuple[MoneyDTO, ...]
    available_liquidity_by_week: tuple[MoneyDTO, ...]


class VarianceRow(FrozenModel):
    category: str
    forecast: MoneyDTO
    actual: MoneyDTO | None = None
    prior_forecast: MoneyDTO | None = None
    variance: MoneyDTO
    material: bool
    explanation: str | None = None


class VarianceBridge(FrozenModel):
    kind: Literal["forecast_vs_actual", "forecast_vs_prior"]
    week_ending: date | None = None
    rows: tuple[VarianceRow, ...]
    closing_variance: MoneyDTO


class AccuracyStatDTO(FrozenModel):
    """Empirical MAPE. This replaces LLM-reported confidence everywhere."""

    category: str
    horizon_weeks: int
    mape_bps: int
    n: int
    window_weeks: int
