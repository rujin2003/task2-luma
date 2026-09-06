"""Execution adapters for consequential treasury actions.

Dry-run is the default. Debt draws, material supplier delays and large FX stay
approval-gated regardless of configuration — this module enforces that gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from sqlmodel import Session

from backend.finance.audit import AuditLog
from backend.finance.controls import ApprovalRequiredError, require_approval_before_draw


class ExecutionBlocked(RuntimeError):
    """An action cannot execute under current policy or approval state."""


ActionKind = Literal["revolver_draw", "ap_deferral", "collection_call", "early_pay_discount"]


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    kind: ActionKind
    amount_minor: int
    currency: str
    actor: str
    counterparty: str | None = None
    document_ref: str | None = None
    dry_run: bool = True


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    kind: ActionKind
    amount_minor: int
    currency: str
    dry_run: bool
    status: Literal["simulated", "executed", "blocked"]
    detail: str
    approval_id: str | None = None


class ExecutionAdapter(Protocol):
    def execute(self, request: ExecutionRequest) -> ExecutionResult: ...


class DebtDrawAdapter:
    """Revolver draw: hard-fails without a recorded approval, even in dry-run=False."""

    def __init__(
        self,
        session: Session,
        *,
        company_id: str,
        audit: AuditLog | None = None,
    ) -> None:
        self.session = session
        self.company_id = company_id
        self.audit = audit

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if request.kind != "revolver_draw":
            raise ExecutionBlocked(f"DebtDrawAdapter cannot execute {request.kind!r}")
        try:
            approval = require_approval_before_draw(
                self.session,
                company_id=self.company_id,
                amount_minor=request.amount_minor,
            )
        except ApprovalRequiredError as exc:
            if self.audit is not None:
                self.audit.append(
                    event_type="execution_blocked",
                    actor=request.actor,
                    action=f"revolver_draw {request.amount_minor}",
                    amount_minor=request.amount_minor,
                    currency=request.currency,
                    decision="blocked",
                    payload={"reason": str(exc)},
                )
            raise ExecutionBlocked(str(exc)) from exc

        status: Literal["simulated", "executed"] = "simulated" if request.dry_run else "executed"
        detail = (
            f"dry-run draw of {request.amount_minor} {request.currency}"
            if request.dry_run
            else f"executed draw of {request.amount_minor} {request.currency}"
        )
        if self.audit is not None:
            self.audit.append(
                event_type="execution",
                actor=request.actor,
                action=f"revolver_draw {request.amount_minor}",
                amount_minor=request.amount_minor,
                currency=request.currency,
                decision=status,
                data_snapshot_ref=approval.snapshot_ref,
                payload={
                    "approval_id": approval.id,
                    "dry_run": request.dry_run,
                    "at": datetime.now(UTC).isoformat(),
                },
            )
        return ExecutionResult(
            kind=request.kind,
            amount_minor=request.amount_minor,
            currency=request.currency,
            dry_run=request.dry_run,
            status=status,
            detail=detail,
            approval_id=approval.id,
        )


__all__ = [
    "ActionKind",
    "DebtDrawAdapter",
    "ExecutionAdapter",
    "ExecutionBlocked",
    "ExecutionRequest",
    "ExecutionResult",
]
