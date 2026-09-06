"""Frozen Pydantic contracts. Shared with Person 2 — change only by agreement."""

from backend.contracts.agent import AgentFinding, AgentStatus
from backend.contracts.approval import ApprovalRequest
from backend.contracts.cash import BankRecon, BankReconItem, CashPosition
from backend.contracts.common import MoneyDTO
from backend.contracts.constraint import Constraint
from backend.contracts.covenant import CovenantStatus
from backend.contracts.debt import DebtCapacity
from backend.contracts.dodo import DodoMetrics
from backend.contracts.evidence import Evidence
from backend.contracts.forecast import (
    AccuracyStatDTO,
    ForecastGrid,
    ForecastLineDTO,
    VarianceBridge,
    VarianceRow,
)
from backend.contracts.recommendation import Recommendation, Strategy
from backend.contracts.stress import StressResult
from backend.contracts.worklist import CollectionOpportunity, DeferralCandidate, WorklistItem

__all__ = [
    "AccuracyStatDTO",
    "AgentFinding",
    "AgentStatus",
    "ApprovalRequest",
    "BankRecon",
    "BankReconItem",
    "CashPosition",
    "CollectionOpportunity",
    "Constraint",
    "CovenantStatus",
    "DebtCapacity",
    "DeferralCandidate",
    "DodoMetrics",
    "Evidence",
    "ForecastGrid",
    "ForecastLineDTO",
    "MoneyDTO",
    "Recommendation",
    "Strategy",
    "StressResult",
    "VarianceBridge",
    "VarianceRow",
    "WorklistItem",
]
