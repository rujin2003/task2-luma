"""Immutable audit log table. Append-only; application code never updates rows."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import CHAR, BigInteger, CheckConstraint, UniqueConstraint
from sqlmodel import Field, SQLModel

from backend.models.base import TenantOwned, new_id, utcnow


class AuditEntry(TenantOwned, SQLModel, table=True):
    """Who approved, when, what they saw, which app version, which data snapshot."""

    __tablename__ = "audit_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_uid", name="uq_audit_entries_uid"),
        CheckConstraint("seq >= 1", name="ck_audit_entries_seq"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    seq: int = Field(index=True)
    event_uid: str = Field(max_length=160)
    event_type: str = Field(max_length=64, index=True)
    actor: str = Field(max_length=128)
    action: str = Field(max_length=512)
    amount_minor: int | None = Field(default=None, sa_type=BigInteger)
    currency: str | None = Field(default=None, sa_type=CHAR(3), max_length=3)
    decision: str | None = Field(default=None, max_length=32)
    data_snapshot_ref: str | None = Field(default=None, max_length=128)
    app_version: str | None = Field(default=None, max_length=32)
    payload: str = Field(default="{}")
    recorded_at: datetime = Field(default_factory=utcnow, index=True)

    def data(self) -> dict[str, Any]:
        decoded: dict[str, Any] = json.loads(self.payload)
        return decoded
