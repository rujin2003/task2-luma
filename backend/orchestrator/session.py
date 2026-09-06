"""In-process session holding the live bus, last cycle, and recommendation."""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.contracts.approvals import ApprovalDecision, ApprovalRequest, ApprovalState
from backend.contracts.events import ApprovalDecided, StatusMark
from backend.contracts.strategy import Recommendation
from backend.orchestrator.bus import EventBus
from backend.orchestrator.investigation import InvestigationResult
from backend.orchestrator.weekly_cycle import CycleResult


@dataclass
class AppSession:
    bus: EventBus = field(default_factory=EventBus)
    cycle: CycleResult | None = None
    investigation: InvestigationResult | None = None
    recommendation: Recommendation | None = None
    approvals: dict[str, ApprovalRequest] = field(default_factory=dict)

    def reset(self) -> None:
        self.bus = EventBus()
        self.cycle = None
        self.investigation = None
        self.recommendation = None
        self.approvals = {}

    def remember_cycle(self, result: CycleResult) -> None:
        self.cycle = result
        self.investigation = result.investigation
        if result.investigation and result.investigation.recommendation:
            self.recommendation = result.investigation.recommendation
            for request in result.investigation.approvals:
                self.approvals[request.request_id] = request

    def decide_approval(self, decision: ApprovalDecision) -> ApprovalRequest:
        request = self.approvals.get(decision.request_id)
        if request is None:
            raise KeyError(f"unknown approval request {decision.request_id}")
        state = ApprovalState.APPROVED if decision.approved else ApprovalState.REJECTED
        updated = request.model_copy(update={"state": state})
        self.approvals[decision.request_id] = updated
        self.bus.emit(
            ApprovalDecided,
            investigation_id=self.investigation.investigation_id if self.investigation else None,
            mark=StatusMark.OK if decision.approved else StatusMark.WARN,
            status_line=(
                f"Approval {'granted' if decision.approved else 'rejected'}: {request.action}"
            )[:200],
            decision=decision,
        )
        return updated


SESSION = AppSession()
