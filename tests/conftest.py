"""Shared builders. Contracts are strict, so tests need valid objects cheaply."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.contracts import (
    AgentFinding,
    AgentRole,
    AgentStatus,
    ApprovalRequest,
    ApprovalRole,
    Evidence,
    Money,
    Provenance,
    SourceSystem,
)

TS = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def provenance() -> Provenance:
    return Provenance(
        source_system=SourceSystem.AR_LEDGER,
        record_id="INV-10482",
        field="amount_due",
        as_of=TS,
        retrieved_at=TS,
    )


@pytest.fixture
def evidence(provenance: Provenance) -> Evidence:
    return Evidence.from_provenance(provenance, "Invoice 10482, $1.2M, 41 days past due")


@pytest.fixture
def finding(evidence: Evidence) -> AgentFinding:
    return AgentFinding(
        agent=AgentRole.AR_COLLECTIONS,
        status=AgentStatus.COMPLETE,
        headline="Found $2.6M realistic acceleration opportunity",
        quantum=Money.from_major("2600000", "USD"),
        evidence=[evidence],
    )


@pytest.fixture
def approval_request(evidence: Evidence) -> ApprovalRequest:
    return ApprovalRequest(
        request_id="req-1",
        action="Draw $2.2M on the revolver",
        amount=Money.from_major("2200000", "USD"),
        expected_impact="Lifts W6 minimum cash above the $20.0M floor",
        risk="Raises revolver utilization to 61%",
        evidence=[evidence],
        why_recommended="Cheapest lever that clears the floor without deferring payroll",
        what_could_go_wrong="A further AR slip would push utilization past the covenant",
        approval_required=ApprovalRole.CFO,
        prepared_by="analyst@novatech",
        created_at=TS,
    )
