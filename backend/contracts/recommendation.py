from __future__ import annotations

from backend.contracts.approval import ApprovalRequest
from backend.contracts.common import FrozenModel, MoneyDTO
from backend.contracts.stress import StressResult
from backend.contracts.worklist import WorklistItem


class Strategy(FrozenModel):
    """A priced bundle of worklist items — not an abstract 'strategy' blob."""

    id: str
    name: str
    items: tuple[WorklistItem, ...]
    rejected_items: tuple[WorklistItem, ...] = ()
    expected_cash_impact: MoneyDTO


class Recommendation(FrozenModel):
    strategy: Strategy
    stress_results: tuple[StressResult, ...]
    rationale: str
    required_approvals: tuple[ApprovalRequest, ...] = ()
    weights_note: str | None = None
