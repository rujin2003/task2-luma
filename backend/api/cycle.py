"""The weekly cycle over HTTP: run it, review it, publish it, check it against policy.

Steps 1-7 are one POST. Steps 8 and 9 are the human's, and they are separate endpoints for
the reason the cycle itself splits there: an override and a publication are decisions with
names attached, and folding them into the automated run would make them look like
side effects of a refresh.

Step 10 is its own call and runs only against a published version. That ordering is the
whole point -- a war room opened on a draft nobody signed is a war room nobody trusts.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.agents.context import Incident
from backend.api.session import Session, get_session
from backend.contracts.approvals import ApprovalRole, Override
from backend.contracts.constraints import ConstraintViolation
from backend.contracts.money import Money
from backend.orchestrator.cycle import CYCLE_STEPS, LAST_AUTOMATED_STEP, CycleResult
from backend.orchestrator.policy import PolicyCheck
from backend.orchestrator.review import PublishedVersion

router = APIRouter(prefix="/api/cycle", tags=["cycle"])

SessionDep = Annotated[Session, Depends(get_session)]


class PolicyCheckResult(BaseModel):
    """The check, plus the three derived answers the UI actually branches on.

    `escalate`, `worst` and `incident` are properties on `PolicyCheck`, and a property does
    not serialise. Recomputing them in the browser would put the rule that decides whether
    a war room opens in two places, in two languages.
    """

    check: PolicyCheck
    escalate: bool
    hard_violations: list[ConstraintViolation] = Field(default_factory=list)
    worst: ConstraintViolation | None = None
    incident: Incident | None = None


def _checked(check: PolicyCheck) -> PolicyCheckResult:
    return PolicyCheckResult(
        check=check,
        escalate=check.escalate,
        hard_violations=check.hard_violations,
        worst=check.worst(),
        incident=check.incident(),
    )


class CycleStatus(BaseModel):
    """Where Monday has got to. The UI asks this on load and renders the right gate."""

    steps: list[str] = list(CYCLE_STEPS)
    last_automated_step: int = LAST_AUTOMATED_STEP
    company: str
    as_of: str
    has_run: bool
    result: CycleResult | None = None
    overrides: list[Override] = Field(default_factory=list)
    published: PublishedVersion | None = None
    policy: PolicyCheckResult | None = None


class OverrideRequest(BaseModel):
    """A human replacing a generated assumption. The reason is required, not optional."""

    target_ref: Annotated[str, Field(min_length=1)]
    field: Annotated[str, Field(min_length=1)]
    previous_value: str
    new_value: str
    previous_amount: Money | None = None
    new_amount: Money | None = None
    reason: Annotated[str, Field(min_length=1, max_length=600)]
    author: Annotated[str, Field(min_length=1)]
    author_role: ApprovalRole
    effective_on: date | None = None


class PublishRequest(BaseModel):
    published_by: Annotated[str, Field(min_length=1)]
    published_by_role: ApprovalRole
    note: Annotated[str, Field(max_length=600)] = ""


def _status(session: Session) -> CycleStatus:
    return CycleStatus(
        company=session.company,
        as_of=session.as_of,
        has_run=session.cycle_result is not None,
        result=session.cycle_result,
        overrides=session.review.overrides,
        published=session.review.published,
        policy=_checked(session.policy) if session.policy else None,
    )


@router.get("")
async def status(session: SessionDep) -> CycleStatus:
    return _status(session)


@router.post("/run")
async def run(session: SessionDep) -> CycleResult:
    """Steps 1-7. Returns at the review gate, which is a human's to open."""
    return await session.run_cycle()


@router.post("/override")
async def override(body: OverrideRequest, session: SessionDep) -> Override:
    """Step 8. Recorded as a first-class object, never applied as a silent edit."""
    recorded = Override(
        forecast_version_id=session.require_cycle().forecast_version_id,
        target_ref=body.target_ref,
        field=body.field,
        previous_value=body.previous_value,
        new_value=body.new_value,
        previous_amount=body.previous_amount,
        new_amount=body.new_amount,
        reason=body.reason,
        author=body.author,
        author_role=body.author_role,
        created_at=datetime.now(UTC),
    )
    return session.record_override(recorded, effective_on=body.effective_on)


@router.post("/publish")
async def publish(body: PublishRequest, session: SessionDep) -> PublishedVersion:
    """Step 9. Locks the version; it becomes next week's baseline."""
    return session.publish(
        published_by=body.published_by,
        published_by_role=body.published_by_role,
        note=body.note,
    )


@router.post("/policy-check")
async def policy_check(session: SessionDep) -> PolicyCheckResult:
    """Step 10. A breach here is what opens the war room, on a dated, quantified figure."""
    return _checked(await session.check_policy())


@router.get("/exceptions")
async def exceptions(session: SessionDep) -> dict[str, Any]:
    """Only what moved materially or where an assumption went stale."""
    result = session.require_cycle()
    return {
        "exceptions": result.exceptions,
        "unexplained": result.unexplained,
        "immaterial_basis": result.immaterial_basis,
        "degraded": result.degraded,
        "degradation_reasons": result.degradation_reasons,
    }
