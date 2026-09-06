from __future__ import annotations

from backend.contracts.common import FrozenModel, MoneyDTO
from backend.contracts.evidence import Evidence


class ApprovalRequest(FrozenModel):
    """Card fields from the spec: ACTION / AMOUNT / IMPACT / RISK / EVIDENCE / ..."""

    action: str
    amount: MoneyDTO
    expected_impact: str
    risk: str
    evidence: tuple[Evidence, ...] = ()
    why_recommended: str
    what_could_go_wrong: str
    approval_required: str
    route: str
