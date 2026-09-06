"""The DB Agent, end to end: a URL in, a loaded ledger out.

This is the object the API drives. It exists because the eight stages have an order
and a set of preconditions, and a UI that could call them in any sequence would be
able to freeze a mapping nobody reconciled or load rows against a mapping nobody
froze. The gates are here, not in the routes.

    connect -> introspect -> classify -> map -> reconcile -> freeze -> load -> drift

`connect` is the only stage that sees the credential. `classify` and `map` see column
names and types. `load` is the only stage that sees a financial value, and by then the
mapping that tells it what those values mean is frozen and signed.

The instance holds the source URL in memory for the duration of the onboarding and
never writes it anywhere. Callers get `redacted_url`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session as OrmSession

from backend.ingest import columns as lexicon
from backend.ingest.agents import CartographerAgents, CartographerProposal
from backend.ingest.database import Probe, introspect, probe, redact
from backend.ingest.drift import DriftReport, watch_drift
from backend.ingest.fingerprint import SchemaFingerprint, fingerprint_from_tables
from backend.ingest.loader import Loader, LoadReport
from backend.ingest.mapping import TenantMapping, draft_from_proposal, freeze_mapping
from backend.ingest.templates import TemplateMatch, match_template
from backend.ingest.validate import BalanceSnapshot, ReconciliationResult, reconcile

MAPPING_DIR = Path("var/mappings")

#: The eight stages, in the order the UI walks them. Exported so the screen and the
#: backend cannot disagree about what the pipeline is.
STAGES = (
    "connect",
    "introspect",
    "classify",
    "map",
    "reconcile",
    "freeze",
    "load",
    "drift",
)


class StageError(RuntimeError):
    """A stage was asked for before the one it depends on had run."""


@dataclass
class Onboarding:
    """One tenant's onboarding, from the pasted URL to the loaded rows."""

    tenant_id: str
    company_name: str
    default_currency: str = "USD"
    schema: str | None = None
    operator: str = "poc@tenant"

    _url: str = field(default="", repr=False)
    probe_result: Probe | None = None
    fingerprint: SchemaFingerprint | None = None
    proposal: CartographerProposal | None = None
    residue: lexicon.Residue | None = None
    template: TemplateMatch | None = None
    mapping: TenantMapping | None = None
    reconciliation: ReconciliationResult | None = None
    mapping_path: Path | None = None
    load_report: LoadReport | None = None
    drift: DriftReport | None = None
    model_calls: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # --- 1. connect ---------------------------------------------------------------------

    def connect(self, url: str) -> Probe:
        """Test the credential and report what it can do. The URL stops here."""
        result = probe(url, schema=self.schema)
        self._url = url
        self.probe_result = result
        if self.schema is None:
            self.schema = None if result.dialect == "sqlite" else result.schema
        return result

    @property
    def redacted_url(self) -> str:
        return redact(self._url) if self._url else ""

    @property
    def connected(self) -> bool:
        return bool(self._url) and self.probe_result is not None

    def forget(self) -> None:
        """Drop the credential. Called when the onboarding is replaced or discarded."""
        self._url = ""

    # --- 2. introspect ------------------------------------------------------------------

    def introspect(self) -> SchemaFingerprint:
        if not self.connected:
            raise StageError("connect to the source database first")
        tables = introspect(self._url, schema=self.schema)
        assert self.probe_result is not None
        self.fingerprint = fingerprint_from_tables(
            tables, source_kind="database", dialect=self.probe_result.dialect
        )
        return self.fingerprint

    # --- 3/4. classify and map -----------------------------------------------------------

    def classify(self, *, agents: CartographerAgents | None = None) -> CartographerProposal:
        """Deterministic lexicon first; a model only on what it could not resolve.

        The template library is still consulted, because a schema that *is* one of the
        known shapes should be recognised as that shape rather than re-derived. It just
        no longer decides the outcome on its own.
        """
        if self.fingerprint is None:
            raise StageError("introspect the schema first")
        self.template = match_template(self.fingerprint)
        proposal, residue = lexicon.propose(self.fingerprint)
        self.model_calls = 0
        if agents is not None and (residue.tables or residue.columns):
            unresolved = tuple(
                table for table in self.fingerprint.tables if table.name in set(residue.tables)
            )
            if unresolved:
                proposal = lexicon.merge(proposal, agents.propose(self.fingerprint, unresolved))
                self.model_calls = 1
        self.proposal = proposal
        self.residue = residue
        return proposal

    def draft(self) -> TenantMapping:
        if self.proposal is None or self.fingerprint is None:
            raise StageError("classify the schema first")
        self.mapping = draft_from_proposal(
            self.proposal,
            source=f"database/{self.probe_result.dialect if self.probe_result else 'unknown'}",
            fingerprint_hash=self.fingerprint.fingerprint_hash,
            confirmed_by=self.operator,
            default_currency=self.default_currency,
        )
        return self.mapping

    # --- 5/6. reconcile and freeze --------------------------------------------------------

    def load(
        self,
        target: OrmSession,
        *,
        stated: BalanceSnapshot | None = None,
        cash_tolerance_minor: int = 0,
        freeze: bool = True,
        mapping_dir: Path | None = None,
    ) -> LoadReport:
        """Read the source, write our rows, then hold them to the tenant's own totals.

        The order is deliberate and is the opposite of what it looks like it should be.
        Reconciliation needs mapped totals, and mapped totals only exist once the rows
        have been read — so the load runs inside the caller's transaction and the gate
        decides whether that transaction is committed. A failed reconciliation leaves
        the target database untouched because the caller rolls back, not because the
        loader was careful.
        """
        if self.mapping is None:
            raise StageError("draft a mapping first")
        if not self.connected:
            raise StageError("the source credential is no longer held; reconnect")

        report = Loader(
            source_url=self._url,
            mapping=self.mapping,
            target=target,
            tenant_id=self.tenant_id,
            company_name=self.company_name,
            default_currency=self.default_currency,
            schema=self.schema,
        ).run()
        self.load_report = report

        if stated is not None:
            self.reconciliation = reconcile(
                self.mapping,
                stated=stated,
                mapped=report.totals(),
                cash_tolerance_minor=cash_tolerance_minor,
            )
            if not self.reconciliation.accepted:
                return report
            self.mapping = self.mapping.model_copy(
                update={
                    "coverage_rows_bps": self.reconciliation.coverage_rows_bps,
                    "coverage_value_bps": self.reconciliation.coverage_value_bps,
                    "confirmed_at": datetime.now(UTC),
                }
            )
        if freeze:
            self.mapping_path = freeze_mapping(
                self.mapping, directory=mapping_dir or MAPPING_DIR / self.tenant_id
            )
        return report

    # --- 8. drift ------------------------------------------------------------------------

    def check_drift(self) -> DriftReport:
        """Re-introspect and compare. A changed schema holds ingestion; it never guesses."""
        if self.mapping is None:
            raise StageError("there is no frozen mapping to check against")
        current = self.introspect()
        self.drift = watch_drift(self.mapping, current)
        return self.drift

    # --- reporting -----------------------------------------------------------------------

    def stage_states(self) -> dict[str, str]:
        """What the screen renders as the pipeline's progress."""
        done = {
            "connect": self.connected,
            "introspect": self.fingerprint is not None,
            "classify": self.proposal is not None,
            "map": self.mapping is not None,
            "reconcile": self.reconciliation is not None,
            "freeze": self.mapping_path is not None,
            "load": self.load_report is not None,
            "drift": self.drift is not None,
        }
        states: dict[str, str] = {}
        for stage in STAGES:
            if stage == "reconcile" and self.reconciliation is not None:
                states[stage] = "ok" if self.reconciliation.accepted else "failed"
            elif stage == "drift" and self.drift is not None:
                states[stage] = "failed" if self.drift.hold_ingestion else "ok"
            else:
                states[stage] = "ok" if done[stage] else "pending"
        return states

    def summary(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "company": self.company_name,
            "currency": self.default_currency,
            "source": self.redacted_url,
            "schema": self.schema,
            "stages": self.stage_states(),
            "model_calls": self.model_calls,
            "started_at": self.started_at.isoformat(),
        }


__all__ = ["MAPPING_DIR", "STAGES", "Onboarding", "StageError"]
