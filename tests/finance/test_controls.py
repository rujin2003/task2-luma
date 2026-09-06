from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine

from backend.contracts.approvals import SegregationOfDutiesError
from backend.finance.controls import (
    ApprovalRequiredError,
    assert_maker_checker,
    require_approval_before_draw,
    route_approval,
)
from backend.models import Approval, ApprovalRoute, Company


def _route(low: int, high: int | None, approver: str) -> ApprovalRoute:
    return ApprovalRoute(
        id=f"route-{low}",
        tenant_id="novatech",
        is_synthetic=True,
        company_id="company-1",
        action="revolver_draw",
        min_amount_minor=low,
        max_amount_minor=high,
        currency="USD",
        approver=approver,
    )


def test_route_approval_uses_half_open_bands() -> None:
    routes = [_route(0, 200_000_000, "cfo"), _route(200_000_000, None, "board")]
    assert route_approval("revolver_draw", 199_999_000, routes).approver == "cfo"
    assert route_approval("revolver_draw", 200_001_000, routes).approver == "board"


def test_maker_checker_requires_three_people() -> None:
    assert_maker_checker("maker", "reviewer", "approver")
    with pytest.raises(SegregationOfDutiesError):
        assert_maker_checker("maker", "reviewer", "MAKER")


def test_revolver_draw_hard_fails_without_approved_record() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Company(
                id="company-1",
                tenant_id="novatech",
                is_synthetic=True,
                name="NovaTech Inc.",
                currency="USD",
            )
        )
        session.commit()
        with pytest.raises(ApprovalRequiredError):
            require_approval_before_draw(session, company_id="company-1", amount_minor=100_000_000)

        approval = Approval(
            id="approval-1",
            tenant_id="novatech",
            is_synthetic=True,
            company_id="company-1",
            action="Revolver draw",
            amount_minor=100_000_000,
            currency="USD",
            required_role="cfo",
            prepared_by="maker",
            decided_by="approver",
            decided_at=datetime(2026, 9, 6, tzinfo=UTC),
            decision="approved",
        )
        session.add(approval)
        session.commit()
        assert (
            require_approval_before_draw(
                session, company_id="company-1", amount_minor=100_000_000
            ).id
            == approval.id
        )
    engine.dispose()
