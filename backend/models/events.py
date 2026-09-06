from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlmodel import Field, SQLModel

from backend.models.base import TenantOwned, derived_id, new_id, one_of, utcnow
from backend.models.vocab import EVENT_TYPES


class FinancialEvent(TenantOwned, SQLModel, table=True):
    """Append-only ledger. Entity tables are projections of these rows.

    Three things make that claim testable rather than decorative:

    * `seq` gives a total order per company, so a replay is deterministic.
    * `event_uid` is the natural key, so replaying an ingestion is idempotent.
    * `payload` is JSON, not a rendered string, so a projection can actually be
      rebuilt from it.
    """

    __tablename__ = "financial_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_uid", name="uq_financial_events_uid"),
        UniqueConstraint("tenant_id", "company_id", "seq", name="uq_financial_events_seq"),
        one_of("event_type", EVENT_TYPES, "financial_events"),
        CheckConstraint("seq >= 1", name="ck_financial_events_seq"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    seq: int = Field(index=True)
    event_uid: str = Field(max_length=160)
    event_type: str = Field(max_length=64, index=True)
    entity_table: str = Field(max_length=64)
    entity_pk: str = Field(max_length=128)
    payload: str
    effective_at: datetime
    recorded_at: datetime = Field(default_factory=utcnow, index=True)

    def data(self) -> dict[str, Any]:
        """Decode the payload. Projections rebuild from this, not from prose."""
        decoded: dict[str, Any] = json.loads(self.payload)
        return decoded


class EventLog:
    """Sequence allocator for one company's event stream.

    Sequence numbers are assigned here rather than by a database identity column
    so a seeded run produces the same stream on every machine and dialect.
    """

    def __init__(self, tenant_id: str, company_id: str, *, is_synthetic: bool) -> None:
        self.tenant_id = tenant_id
        self.company_id = company_id
        self.is_synthetic = is_synthetic
        self._seq = 0

    def emit(
        self,
        event_type: str,
        entity_table: str,
        entity_pk: str,
        payload: dict[str, Any],
        effective_at: datetime,
        recorded_at: datetime | None = None,
    ) -> FinancialEvent:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event_type {event_type!r}")
        self._seq += 1
        event_uid = f"{event_type}:{entity_table}:{entity_pk}:{self._seq}"
        return FinancialEvent(
            id=derived_id("financial_events", self.company_id, event_uid),
            tenant_id=self.tenant_id,
            is_synthetic=self.is_synthetic,
            company_id=self.company_id,
            seq=self._seq,
            event_uid=event_uid,
            event_type=event_type,
            entity_table=entity_table,
            entity_pk=entity_pk,
            payload=json.dumps(payload, sort_keys=True, separators=(",", ":")),
            effective_at=effective_at,
            recorded_at=recorded_at if recorded_at is not None else effective_at,
        )
