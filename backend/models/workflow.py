from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import CHAR, BigInteger, CheckConstraint, UniqueConstraint
from sqlmodel import Field, SQLModel

from backend.models.base import (
    Bitemporal,
    Sourced,
    TenantOwned,
    new_id,
    non_negative,
    one_of,
    valid_currency,
)
from backend.models.vocab import (
    AGENT_RUN_STATUSES,
    APPROVAL_DECISIONS,
    APPROVER_ROLES,
    RECOMMENDATION_STATUSES,
    WORKLIST_STATUSES,
)


class WorklistItem(TenantOwned, Bitemporal, SQLModel, table=True):
    __tablename__ = "worklist_items"
    __table_args__ = (
        non_negative("amount_minor", "worklist_items"),
        valid_currency("currency", "worklist_items"),
        one_of("status", WORKLIST_STATUSES, "worklist_items"),
        CheckConstraint(
            "status <> 'rejected' OR rejection_reason IS NOT NULL",
            name="ck_worklist_items_rejection_reason",
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    owner: str = Field(max_length=128)
    action: str = Field(max_length=512)
    counterparty: str | None = Field(default=None, max_length=256)
    document_ref: str | None = Field(default=None, max_length=64)
    amount_minor: int = Field(sa_type=BigInteger)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    due_date: date = Field(index=True)
    status: str = Field(default="open", max_length=32, index=True)
    rejection_reason: str | None = Field(default=None, max_length=1024)
    prepared_by: str | None = Field(default=None, max_length=64)
    reviewed_by: str | None = Field(default=None, max_length=64)


class ApprovalRoute(TenantOwned, SQLModel, table=True):
    """One band of the delegation-of-authority matrix.

    Bands are half-open `[min_amount_minor, max_amount_minor)` so an amount $10
    over a band cannot fall in two bands or in none. `max_amount_minor IS NULL`
    is the open-ended top band.
    """

    __tablename__ = "approval_routes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "action", "min_amount_minor", name="uq_approval_routes_band"),
        non_negative("min_amount_minor", "approval_routes"),
        valid_currency("currency", "approval_routes"),
        one_of("approver", APPROVER_ROLES, "approval_routes"),
        CheckConstraint(
            "max_amount_minor IS NULL OR max_amount_minor > min_amount_minor",
            name="ck_approval_routes_band_order",
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    action: str = Field(max_length=64, index=True)
    min_amount_minor: int = Field(default=0, sa_type=BigInteger)
    max_amount_minor: int | None = Field(default=None, sa_type=BigInteger)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    approver: str = Field(max_length=32)
    blocked: bool = False


class Approval(TenantOwned, SQLModel, table=True):
    """An approval decision with the segregation-of-duties rule in the schema.

    Maker-checker is enforced here as a CHECK, not only in service code: a row
    where the preparer is also the approver cannot be written at all.
    """

    __tablename__ = "approvals"
    __table_args__ = (
        non_negative("amount_minor", "approvals"),
        valid_currency("currency", "approvals"),
        one_of("decision", APPROVAL_DECISIONS, "approvals"),
        one_of("required_role", APPROVER_ROLES, "approvals"),
        CheckConstraint(
            "decided_by IS NULL OR prepared_by IS NULL OR decided_by <> prepared_by",
            name="ck_approvals_segregation_of_duties",
        ),
        CheckConstraint(
            "decision = 'pending' OR (decided_by IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_approvals_decided_fields",
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    worklist_item_id: str | None = Field(default=None, foreign_key="worklist_items.id", index=True)
    action: str = Field(max_length=512)
    amount_minor: int = Field(sa_type=BigInteger)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    required_role: str = Field(max_length=32)
    prepared_by: str | None = Field(default=None, max_length=64)
    decided_by: str | None = Field(default=None, max_length=64)
    decided_at: datetime | None = None
    decision: str = Field(default="pending", max_length=16, index=True)
    snapshot_ref: str | None = Field(default=None, max_length=128)
    app_version: str | None = Field(default=None, max_length=32)


class Scenario(TenantOwned, SQLModel, table=True):
    __tablename__ = "scenarios"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "name", name="uq_scenarios_name"),
        valid_currency("currency", "scenarios"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    version_id: str | None = Field(default=None, foreign_key="forecast_versions.id", index=True)
    name: str = Field(max_length=128)
    expected_cash_impact_minor: int = Field(sa_type=BigInteger)
    currency: str = Field(sa_type=CHAR(3), max_length=3)


class StressTest(TenantOwned, SQLModel, table=True):
    __tablename__ = "stress_tests"
    __table_args__ = (
        UniqueConstraint("scenario_id", "stressor", name="uq_stress_tests_stressor"),
        valid_currency("currency", "stress_tests"),
        CheckConstraint(
            "passed = TRUE OR failure_reason IS NOT NULL", name="ck_stress_tests_failure_reason"
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    scenario_id: str = Field(foreign_key="scenarios.id", index=True)
    stressor: str = Field(max_length=64)
    passed: bool
    min_cash_minor: int = Field(sa_type=BigInteger)
    threshold_minor: int = Field(sa_type=BigInteger)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    failure_reason: str | None = Field(default=None, max_length=512)


class Recommendation(TenantOwned, SQLModel, table=True):
    __tablename__ = "recommendations"
    __table_args__ = (one_of("status", RECOMMENDATION_STATUSES, "recommendations"),)

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    scenario_id: str | None = Field(default=None, foreign_key="scenarios.id")
    agent_run_id: str | None = Field(default=None, foreign_key="agent_runs.id", index=True)
    rationale: str = Field(max_length=2048)
    status: str = Field(default="draft", max_length=32, index=True)


class AgentRun(TenantOwned, SQLModel, table=True):
    __tablename__ = "agent_runs"
    __table_args__ = (
        one_of("status", AGENT_RUN_STATUSES, "agent_runs"),
        non_negative("input_tokens", "agent_runs"),
        non_negative("output_tokens", "agent_runs"),
        non_negative("latency_ms", "agent_runs"),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at", name="ck_agent_runs_finish_order"
        ),
        CheckConstraint(
            "status = 'running' OR finished_at IS NOT NULL", name="ck_agent_runs_finished"
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    agent: str = Field(max_length=64, index=True)
    status: str = Field(max_length=32)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    failure_reason: str | None = Field(default=None, max_length=512)
    started_at: datetime
    finished_at: datetime | None = None


class EvidenceRow(TenantOwned, Sourced, Bitemporal, SQLModel, table=True):
    """A citation: a table, a row, a field, and what we knew when.

    `source_table` and `source_pk` are the resolvable reference the evidence
    validator checks, which is what stops an agent citing a row that does not
    exist.
    """

    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint(
            "agent_run_id", "source_table", "source_pk", "field", name="uq_evidence_citation"
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    agent_run_id: str | None = Field(default=None, foreign_key="agent_runs.id", index=True)
    source: str = Field(max_length=64)
    reference: str = Field(max_length=128)
    field: str = Field(max_length=64)
