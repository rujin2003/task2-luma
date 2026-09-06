"""Stage 7 — drift watch.

Hash the schema fingerprint on every sync. On change, hold ingestion rather than
importing silently corrupted data.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.ingest.fingerprint import SchemaFingerprint
from backend.ingest.mapping import TenantMapping


@dataclass(frozen=True, slots=True)
class DriftReport:
    changed: bool
    previous_hash: str
    current_hash: str
    hold_ingestion: bool
    reason: str | None = None


def watch_drift(
    mapping: TenantMapping,
    current: SchemaFingerprint,
    *,
    hold_on_change: bool = True,
) -> DriftReport:
    previous = mapping.fingerprint_hash
    current_hash = current.fingerprint_hash or current.with_hash().fingerprint_hash
    if previous == current_hash:
        return DriftReport(
            changed=False,
            previous_hash=previous,
            current_hash=current_hash,
            hold_ingestion=False,
        )
    return DriftReport(
        changed=True,
        previous_hash=previous,
        current_hash=current_hash,
        hold_ingestion=hold_on_change,
        reason="schema fingerprint changed; re-run cartographer delta before ingesting",
    )


__all__ = ["DriftReport", "watch_drift"]
