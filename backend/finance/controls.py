"""Deterministic approval routing and segregation-of-duties controls."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlmodel import Session, select

from backend.contracts.approvals import SegregationOfDutiesError
from backend.models.workflow import Approval


class ApprovalRequiredError(RuntimeError):
    """A consequential action has no matching approved authorization."""


def _route_action(route: Any) -> str | None:
    return getattr(route, "action", getattr(route, "action_class", None))


def _bound_minor(route: Any, name: str, contract_name: str) -> int | None:
    value = getattr(route, name, None)
    if value is not None:
        return int(value)
    money = getattr(route, contract_name, None)
    return None if money is None else int(money.minor_units)


def route_approval(action: str, amount_minor: int, routes: Sequence[Any]) -> Any:
    """Select the unique half-open delegation-of-authority band."""
    if amount_minor < 0:
        raise ValueError("amount_minor cannot be negative")
    matches = []
    for route in routes:
        if _route_action(route) != action:
            continue
        lower = _bound_minor(route, "min_amount_minor", "lower_bound") or 0
        upper = _bound_minor(route, "max_amount_minor", "upper_bound")
        if lower <= amount_minor and (upper is None or amount_minor < upper):
            matches.append(route)
    if not matches:
        raise ApprovalRequiredError(
            f"no approval route covers {action!r} at {amount_minor} minor units"
        )
    if len(matches) != 1:
        raise ValueError(f"approval routes overlap for {action!r} at {amount_minor}")
    return matches[0]


def assert_maker_checker(preparer: str, reviewer: str, approver: str) -> None:
    """Require three distinct participants, compared case-insensitively."""
    participants = [value.strip().casefold() for value in (preparer, reviewer, approver)]
    if any(not participant for participant in participants):
        raise SegregationOfDutiesError("preparer, reviewer and approver are required")
    if len(set(participants)) != 3:
        raise SegregationOfDutiesError("preparer, reviewer and approver must be different people")


def assert_worklist_approver(prepared_by: str, approver: str) -> None:
    """The analyst who prepared a worklist cannot approve it."""
    if not prepared_by.strip() or not approver.strip():
        raise SegregationOfDutiesError("prepared_by and approver are required")
    if prepared_by.strip().casefold() == approver.strip().casefold():
        raise SegregationOfDutiesError(
            f"{approver} prepared this worklist and cannot also approve it"
        )


def require_approval_before_draw(
    session: Session,
    *,
    company_id: str,
    amount_minor: int,
) -> Approval:
    """Return the matching approved draw authorization or hard-fail."""
    approvals = session.exec(
        select(Approval).where(
            Approval.company_id == company_id,
            Approval.decision == "approved",
            Approval.amount_minor >= amount_minor,
        )
    ).all()
    matches = [
        approval
        for approval in approvals
        if "draw" in approval.action.casefold() or "revolver" in approval.action.casefold()
    ]
    if not matches:
        raise ApprovalRequiredError(
            f"revolver draw of {amount_minor} minor units has no approved Approval record"
        )
    return min(matches, key=lambda approval: approval.amount_minor)


__all__ = [
    "ApprovalRequiredError",
    "assert_maker_checker",
    "assert_worklist_approver",
    "require_approval_before_draw",
    "route_approval",
]
