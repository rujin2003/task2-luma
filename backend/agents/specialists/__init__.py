"""The six specialists.

Each is a prompt in `prompts/`, a tool allowlist in `backend/tools/registry.py`, a
`gather()` that renders decisions as brief lines, and -- where policy needs the last word --
a deterministic `review()`. Everything else they share, so their failures look alike.

`build` is what the Commander calls: give it a role, get the specialist configured for this
investigation. It is a function rather than a dict of instances because two of them carry
per-run state (the week under review, the proposals being challenged), and a shared
instance would leak one investigation into the next.
"""

from __future__ import annotations

from typing import Any

from backend.contracts.agent import AgentRole

from .ap_optimization import ApOptimizationAgent
from .ar_collections import ArCollectionsAgent
from .base import Specialist
from .dodo_revenue import DodoRevenueAgent
from .forecast import ForecastAgent
from .supplier_risk import SupplierRiskAgent
from .variance import VarianceAgent

SPECIALISTS: dict[AgentRole, type[Specialist]] = {
    AgentRole.FORECAST: ForecastAgent,
    AgentRole.VARIANCE: VarianceAgent,
    AgentRole.AR_COLLECTIONS: ArCollectionsAgent,
    AgentRole.AP_OPTIMIZATION: ApOptimizationAgent,
    AgentRole.SUPPLIER_RISK: SupplierRiskAgent,
    AgentRole.DODO_REVENUE: DodoRevenueAgent,
}


def build(role: AgentRole, **options: Any) -> Specialist:
    """One fresh specialist, configured for this run."""
    if role not in SPECIALISTS:
        raise KeyError(f"{role.value} is not one of the six specialists")
    return SPECIALISTS[role](**options)


__all__ = [
    "SPECIALISTS",
    "ApOptimizationAgent",
    "ArCollectionsAgent",
    "DodoRevenueAgent",
    "ForecastAgent",
    "Specialist",
    "SupplierRiskAgent",
    "VarianceAgent",
    "build",
]
