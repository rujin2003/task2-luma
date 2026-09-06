"""A `Toolset` backed by recorded JSON. This is what the whole product is built against
until Person 1's engine lands behind the same signatures at Merge Point 1.

It is not a mock in the throwaway sense: the fixtures are validated into the same payload
models the engine will return, so a fixture that drifts from the contract fails the test
suite rather than passing a bad shape downstream. It is also what makes the golden-path
demo deterministic -- same numbers, every run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.contracts.provenance import SourceSystem
from backend.tools.results import (
    ArAgingSummary,
    CapabilityManifest,
    CollectionOpportunities,
    CovenantStatus,
    DeferralCandidates,
    DodoDeclineBreakdown,
    DriverAssumptions,
    EvidenceRow,
    ForecastErrorPercentiles,
    ForecastSummary,
    LiquidityPosition,
    PolicyConstraints,
    SupplierRiskProfile,
    ToolPayload,
    VarianceBridge,
    cap,
)
from backend.tools.toolset import DEFAULT_TOP_N, MAX_ROWS, ToolError

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "tools"


class FixtureToolset:
    """Every method reads one JSON file named for the tool that returns it."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or FIXTURE_ROOT

    # --- loading ---------------------------------------------------------------------

    def _load(self, tool: str) -> Any:
        path = self.root / f"{tool}.json"
        if not path.exists():
            raise ToolError(f"no fixture for {tool} at {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _payload[PayloadT: ToolPayload](self, tool: str, model: type[PayloadT]) -> PayloadT:
        return model.model_validate(self._load(tool))

    @staticmethod
    def _limit(top_n: int) -> int:
        if top_n < 1:
            raise ToolError("top_n must be at least 1")
        return min(top_n, MAX_ROWS)

    # --- shared position -------------------------------------------------------------

    async def get_liquidity_position(self) -> LiquidityPosition:
        return self._payload("get_liquidity_position", LiquidityPosition)

    async def get_covenant_status(self) -> CovenantStatus:
        return self._payload("get_covenant_status", CovenantStatus)

    async def get_policy_constraints(self) -> PolicyConstraints:
        return self._payload("get_policy_constraints", PolicyConstraints)

    async def get_capability_manifest(self) -> CapabilityManifest:
        return self._payload("get_capability_manifest", CapabilityManifest)

    # --- forecast and variance -------------------------------------------------------

    async def get_forecast_summary(self, version_id: str | None = None) -> ForecastSummary:
        summary = self._payload("get_forecast_summary", ForecastSummary)
        if version_id is not None and version_id != summary.version_id:
            raise ToolError(f"no fixture for forecast version {version_id}")
        return summary

    async def list_driver_assumptions(
        self, *, top_n: int = DEFAULT_TOP_N, stale_only: bool = False
    ) -> DriverAssumptions:
        payload = self._payload("list_driver_assumptions", DriverAssumptions)
        rows = [row for row in payload.rows if row.stale] if stale_only else payload.rows
        rows, truncation = cap(rows, self._limit(top_n))
        return payload.model_copy(
            update={
                "rows": rows,
                "truncation": truncation,
                "references": [row.reference for row in rows],
            }
        )

    async def get_variance_bridge(
        self, *, week_ending: str | None = None, top_n: int = DEFAULT_TOP_N
    ) -> VarianceBridge:
        payload = self._payload("get_variance_bridge", VarianceBridge)
        if week_ending is not None and week_ending != payload.week_ending.isoformat():
            raise ToolError(f"no fixture for week ending {week_ending}")
        rows, truncation = cap(payload.rows, self._limit(top_n))
        return payload.model_copy(
            update={
                "rows": rows,
                "truncation": truncation,
                "references": [row.reference for row in rows],
            }
        )

    async def get_forecast_error_percentiles(
        self, *, horizon_weeks: int
    ) -> ForecastErrorPercentiles:
        payload = self._payload("get_forecast_error_percentiles", ForecastErrorPercentiles)
        percentiles = [p for p in payload.percentiles if p.horizon_weeks == horizon_weeks]
        if not percentiles:
            raise ToolError(f"no measured error at horizon {horizon_weeks}")
        return payload.model_copy(update={"percentiles": percentiles})

    # --- receivables -----------------------------------------------------------------

    async def rank_collection_opportunities(
        self, *, top_n: int = DEFAULT_TOP_N
    ) -> CollectionOpportunities:
        payload = self._payload("rank_collection_opportunities", CollectionOpportunities)
        rows, truncation = cap(payload.rows, self._limit(top_n))
        return payload.model_copy(
            update={
                "rows": rows,
                "truncation": truncation,
                "references": [row.reference for row in rows],
            }
        )

    async def get_ar_aging_summary(self) -> ArAgingSummary:
        return self._payload("get_ar_aging_summary", ArAgingSummary)

    # --- payables and supplier risk --------------------------------------------------

    async def rank_deferral_candidates(
        self, *, top_n: int = DEFAULT_TOP_N, max_delay_days: int = 30
    ) -> DeferralCandidates:
        payload = self._payload("rank_deferral_candidates", DeferralCandidates)
        # Protected rows stay in the result, marked. An agent must be able to see that
        # payroll exists and is off limits, rather than wondering where it went.
        rows = [
            row for row in payload.rows if row.protected or row.max_delay_days <= max_delay_days
        ]
        rows, truncation = cap(rows, self._limit(top_n))
        return payload.model_copy(
            update={
                "rows": rows,
                "truncation": truncation,
                "references": [row.reference for row in rows],
            }
        )

    async def get_supplier_risk_profile(self, *, supplier: str) -> SupplierRiskProfile:
        profiles = self._load("get_supplier_risk_profile")
        if supplier not in profiles:
            raise ToolError(f"no supplier risk profile for {supplier!r}")
        return SupplierRiskProfile.model_validate(profiles[supplier])

    # --- dodo ------------------------------------------------------------------------

    async def get_dodo_decline_breakdown(
        self, *, top_n: int = DEFAULT_TOP_N
    ) -> DodoDeclineBreakdown:
        payload = self._payload("get_dodo_decline_breakdown", DodoDeclineBreakdown)
        rows, truncation = cap(payload.rows, self._limit(top_n))
        return payload.model_copy(
            update={
                "rows": rows,
                "truncation": truncation,
                "references": [row.reference for row in rows],
            }
        )

    # --- evidence --------------------------------------------------------------------

    async def resolve_evidence(self, *, reference: str) -> EvidenceRow:
        rows = self._load("resolve_evidence")
        row = rows.get(reference)
        if row is None:
            # Not an error: an unresolvable reference is a finding to reject, and the
            # validator needs the answer rather than an exception.
            source = reference.split(":", 1)[0]
            return EvidenceRow(
                reference=reference,
                source=SourceSystem(source) if source in set(SourceSystem) else SourceSystem.GL,
                excerpt="",
                resolved=False,
            )
        return EvidenceRow.model_validate(row)
