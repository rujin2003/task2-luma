"""Schema Cartographer and tenant ETL. Phase 2A.

Six stages are deterministic. Exactly one (residue mapping) may call a model,
and only on what template match could not resolve. No raw financial rows are
ever sent to a model — column names, types and format signatures only.
"""

from backend.ingest.agents import (
    CartographerAgents,
    CartographerProposal,
    NullCartographerAgents,
)
from backend.ingest.connect import CsvSource, introspect_csv
from backend.ingest.drift import DriftReport, watch_drift
from backend.ingest.fingerprint import (
    SchemaFingerprint,
    fingerprint_from_tables,
    infer_format_signature,
)
from backend.ingest.mapping import (
    CapabilityManifest,
    TenantMapping,
    draft_from_template,
    freeze_mapping,
    load_mapping,
)
from backend.ingest.pipeline import OnboardingResult, check_existing_mapping_drift, onboard_csv
from backend.ingest.templates import TemplateMatch, match_template
from backend.ingest.validate import BalanceSnapshot, MappedTotals, reconcile

__all__ = [
    "BalanceSnapshot",
    "CapabilityManifest",
    "CartographerAgents",
    "CartographerProposal",
    "CsvSource",
    "DriftReport",
    "MappedTotals",
    "NullCartographerAgents",
    "OnboardingResult",
    "SchemaFingerprint",
    "TemplateMatch",
    "TenantMapping",
    "check_existing_mapping_drift",
    "draft_from_template",
    "fingerprint_from_tables",
    "freeze_mapping",
    "infer_format_signature",
    "introspect_csv",
    "load_mapping",
    "match_template",
    "onboard_csv",
    "reconcile",
    "watch_drift",
]
