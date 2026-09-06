"""Approvals over HTTP: the cards, the decisions, the audit trail and the execution gate.

Nothing here decides anything. Classification, routing, maker-checker and the gate all
live in `backend/orchestrator/approvals.py`, and this module's whole job is to expose them
without adding a way around them. In particular there is deliberately no endpoint that
executes a row while marking it approved in the same call: that convenience is exactly
what segregation of duties exists to prevent.

The decision endpoint returns the audit entry rather than a bare 204, because the entry is
the thing worth having -- who signed, when, and the card as it was rendered to them at the
moment they signed it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.api.session import Session, get_session
from backend.contracts.approvals import (
    APPROVAL_CARD_FIELDS,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalRole,
    ApprovalState,
)
from backend.contracts.strategy import RejectedAction, WorklistItem
from backend.orchestrator.approvals import AuditEntry, RiskVerdict

router = APIRouter(prefix="/api/approvals", tags=["approvals"])

SessionDep = Annotated[Session, Depends(get_session)]


class ApprovalCard(BaseModel):
    """One request, its rendered card, its verdict and where it currently stands."""

    request: ApprovalRequest
    card: dict[str, str]
    verdict: RiskVerdict | None = None
    state: ApprovalState
    decision: ApprovalDecision | None = None


class ApprovalBoard(BaseModel):
    """Everything the approvals screen renders, in one call."""

    field_order: list[str] = list(APPROVAL_CARD_FIELDS)
    worklist: list[WorklistItem] = Field(default_factory=list)
    cards: list[ApprovalCard] = Field(default_factory=list)
    # Surfaced with reason and evidence. An experienced treasurer looks for this first.
    rejected: list[RejectedAction] = Field(default_factory=list)


class DecisionRequest(BaseModel):
    decided_by: Annotated[str, Field(min_length=1)]
    decided_by_role: ApprovalRole
    approved: bool
    reason: Annotated[str, Field(max_length=600)] = ""


@router.get("")
async def board(session: SessionDep) -> ApprovalBoard:
    """Prepare the cards if they do not exist yet, then render the board."""
    pack = await session.approval_pack()
    return ApprovalBoard(
        worklist=pack.worklist,
        cards=[
            ApprovalCard(
                request=request,
                card=request.card(),
                verdict=pack.verdicts.get(request.worklist_seq or -1),
                state=session.state_of(request.request_id),
                decision=session.approvals.decision_for(request.request_id),
            )
            for request in pack.requests
        ],
        rejected=pack.blocked,
    )


@router.post("/{request_id}/decide")
async def decide(request_id: str, body: DecisionRequest, session: SessionDep) -> AuditEntry:
    """Sign or refuse one card. Every control that can refuse this refuses it here."""
    await session.approval_pack()
    decision = ApprovalDecision(
        request_id=request_id,
        decided_by=body.decided_by,
        decided_by_role=body.decided_by_role,
        approved=body.approved,
        reason=body.reason,
        decided_at=datetime.now(UTC),
    )
    return session.decide(decision)


@router.post("/worklist/{seq}/execute")
async def execute(seq: int, session: SessionDep) -> dict[str, object]:
    """Execute one row through the gate. Dry-run by default and by design."""
    await session.approval_pack()
    receipt = await session.execute(seq)
    return {"seq": seq, "dry_run": True, "receipt": receipt}


@router.get("/audit")
async def audit(session: SessionDep) -> list[AuditEntry]:
    """Append-only: who approved, when, and the card they actually saw."""
    return session.audit.entries


@router.get("/{request_id}")
async def card(request_id: str, session: SessionDep) -> ApprovalCard:
    pack = await session.approval_pack()
    request = session.approvals.get(request_id)
    if request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no approval request {request_id}"
        )
    return ApprovalCard(
        request=request,
        card=request.card(),
        verdict=pack.verdicts.get(request.worklist_seq or -1),
        state=session.state_of(request_id),
        decision=session.approvals.decision_for(request_id),
    )
