"""Schema Cartographer pipeline — six deterministic stages, one optional model call.

Stages: connect → introspect → template match → (agent residue) → validate →
confirm → freeze → drift watch.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.ingest.agents import CartographerAgents, CartographerProposal, NullCartographerAgents
from backend.ingest.connect import CsvSource, introspect_csv
from backend.ingest.drift import DriftReport, watch_drift
from backend.ingest.fingerprint import SchemaFingerprint, fingerprint_from_tables
from backend.ingest.mapping import TenantMapping, draft_from_template, freeze_mapping
from backend.ingest.templates import TemplateMatch, match_template
from backend.ingest.validate import (
    BalanceSnapshot,
    MappedTotals,
    ReconciliationResult,
    reconcile,
)


@dataclass(frozen=True, slots=True)
class OnboardingResult:
    fingerprint: SchemaFingerprint
    template: TemplateMatch | None
    proposal: CartographerProposal
    mapping: TenantMapping | None
    reconciliation: ReconciliationResult | None
    mapping_path: Path | None
    model_calls: int
    drift: DriftReport | None = None


def onboard_csv(
    directory: Path,
    *,
    stated: BalanceSnapshot,
    mapped: MappedTotals,
    output_dir: Path,
    confirmed_by: str = "analyst@novatech.example",
    agents: CartographerAgents | None = None,
    cash_tolerance_minor: int = 0,
) -> OnboardingResult:
    """Run the cartographer against a CSV upload (the universal fallback / demo path)."""
    source = CsvSource(directory)
    assert source.read_only
    tables = introspect_csv(directory)
    fingerprint = fingerprint_from_tables(tables, source_kind="csv", dialect="csv")
    template = match_template(fingerprint)
    model_calls = 0
    proposal = CartographerProposal()
    agent_runtime = agents or NullCartographerAgents()

    if template is None or template.residual_tables:
        residue = tuple(
            t for t in fingerprint.tables if template is None or t.name in template.residual_tables
        )
        if residue:
            proposal = agent_runtime.propose(fingerprint, residue)
            model_calls = 1

    mapping: TenantMapping | None = None
    mapping_path: Path | None = None
    if template is not None:
        mapping = draft_from_template(
            template_id=template.template_id,
            source=f"csv/{template.template_id}",
            fingerprint_hash=fingerprint.fingerprint_hash,
            confirmed_by=confirmed_by,
        )

    reconciliation: ReconciliationResult | None = None
    if mapping is not None:
        reconciliation = reconcile(
            mapping,
            stated=stated,
            mapped=mapped,
            cash_tolerance_minor=cash_tolerance_minor,
        )
        if reconciliation.accepted:
            mapping = mapping.model_copy(
                update={
                    "coverage_rows_bps": reconciliation.coverage_rows_bps,
                    "coverage_value_bps": reconciliation.coverage_value_bps,
                }
            )
            mapping_path = freeze_mapping(mapping, directory=output_dir)

    return OnboardingResult(
        fingerprint=fingerprint,
        template=template,
        proposal=proposal,
        mapping=mapping,
        reconciliation=reconciliation,
        mapping_path=mapping_path,
        model_calls=model_calls,
    )


def check_existing_mapping_drift(
    mapping: TenantMapping,
    fingerprint: SchemaFingerprint,
) -> DriftReport:
    return watch_drift(mapping, fingerprint)


__all__ = [
    "OnboardingResult",
    "check_existing_mapping_drift",
    "onboard_csv",
]
