"""Immutable audit log for consequential treasury actions.

Append-only: once written, rows are never updated or deleted by application code.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

from backend.models.audit import AuditEntry


class AuditLog:
    """Sequence allocator that never rewrites history."""

    def __init__(
        self,
        session: Session,
        *,
        tenant_id: str,
        company_id: str,
        is_synthetic: bool = True,
        app_version: str | None = "0.1.0",
    ) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self.company_id = company_id
        self.is_synthetic = is_synthetic
        self.app_version = app_version
        self._seq = self._max_seq()

    def _max_seq(self) -> int:
        rows = self.session.exec(
            select(AuditEntry).where(
                AuditEntry.tenant_id == self.tenant_id,
                AuditEntry.company_id == self.company_id,
            )
        ).all()
        return max((row.seq for row in rows), default=0)

    def append(
        self,
        *,
        event_type: str,
        actor: str,
        action: str,
        amount_minor: int | None = None,
        currency: str | None = None,
        decision: str | None = None,
        data_snapshot_ref: str | None = None,
        payload: dict[str, Any] | None = None,
        recorded_at: datetime | None = None,
    ) -> AuditEntry:
        self._seq += 1
        event_uid = f"{event_type}:{self.company_id}:{self._seq}"
        entry = AuditEntry(
            tenant_id=self.tenant_id,
            is_synthetic=self.is_synthetic,
            company_id=self.company_id,
            seq=self._seq,
            event_uid=event_uid,
            event_type=event_type,
            actor=actor,
            action=action,
            amount_minor=amount_minor,
            currency=currency,
            decision=decision,
            data_snapshot_ref=data_snapshot_ref,
            app_version=self.app_version,
            payload=json.dumps(payload or {}, sort_keys=True, separators=(",", ":")),
            recorded_at=recorded_at or datetime.now(UTC),
        )
        self.session.add(entry)
        self.session.flush()
        return entry


__all__ = ["AuditEntry", "AuditLog"]
