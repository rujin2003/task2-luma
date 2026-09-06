from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from backend.contracts.common import FrozenModel
from backend.contracts.constraint import Constraint
from backend.contracts.evidence import Evidence
from backend.contracts.forecast import AccuracyStatDTO

AgentStatus = Literal["complete", "degraded", "failed", "refused"]


class AgentFinding(FrozenModel):
    """Structured agent output. No prose-only responses. No chain-of-thought."""

    agent: str
    status: AgentStatus
    finding: dict[str, Any] = Field(default_factory=dict)
    evidence: tuple[Evidence, ...] = ()
    risks: tuple[str, ...] = ()
    recommended_actions: tuple[str, ...] = ()
    requires_followup: bool = False
    constraint_violations: tuple[Constraint, ...] = ()
    accuracy: AccuracyStatDTO | None = None
    degraded_reason: str | None = None
