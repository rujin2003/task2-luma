from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from backend.contracts.approvals import SegregationOfDutiesError
from backend.finance.audit import AuditLog
from backend.finance.controls import (
    assert_worklist_approver,
    require_approval_before_draw,
    route_approval,
)
from backend.finance.execution import DebtDrawAdapter, ExecutionBlocked, ExecutionRequest
from backend.models import Approval, ApprovalRoute, AuditEntry, Company


def _session() -> tuple:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine, Session(engine)


def _company(session: Session) -> None:
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


def test_ten_dollars_over_band_routes_higher() -> None:
    """$10 over a DoA band is half-open: falls in the next band, not the lower one."""
    routes = [
        ApprovalRoute(
            id="a",
            tenant_id="novatech",
            is_synthetic=True,
            company_id="company-1",
            action="ap_deferral",
            min_amount_minor=0,
            max_amount_minor=25_000_000,
            currency="USD",
            approver="treasurer",
        ),
        ApprovalRoute(
            id="b",
            tenant_id="novatech",
            is_synthetic=True,
            company_id="company-1",
            action="ap_deferral",
            min_amount_minor=25_000_000,
            max_amount_minor=100_000_000,
            currency="USD",
            approver="cfo",
        ),
    ]
    assert route_approval("ap_deferral", 25_000_000, routes).approver == "cfo"
    assert route_approval("ap_deferral", 25_000_010, routes).approver == "cfo"


def test_preparer_cannot_approve_own_worklist() -> None:
    assert_worklist_approver("A. Rivera", "M. Chen")
    with pytest.raises(SegregationOfDutiesError):
        assert_worklist_approver("A. Rivera", "a. rivera")


def test_debt_draw_adapter_requires_approval_and_audits() -> None:
    engine, session = _session()
    try:
        _company(session)
        audit = AuditLog(session, tenant_id="novatech", company_id="company-1")
        adapter = DebtDrawAdapter(session, company_id="company-1", audit=audit)
        request = ExecutionRequest(
            kind="revolver_draw",
            amount_minor=100_000_000,
            currency="USD",
            actor="treasury",
            dry_run=False,
        )
        with pytest.raises(ExecutionBlocked):
            adapter.execute(request)

        session.add(
            Approval(
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
                snapshot_ref="snap-1",
            )
        )
        session.commit()
        result = adapter.execute(request)
        assert result.status == "executed"
        assert result.approval_id == "approval-1"
        logged = session.exec(select(AuditEntry)).all()
        assert any(row.event_type == "execution" for row in logged)
        assert (
            require_approval_before_draw(
                session, company_id="company-1", amount_minor=50_000_000
            ).id
            == "approval-1"
        )
    finally:
        session.close()
        engine.dispose()
