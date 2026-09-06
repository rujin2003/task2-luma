"""The tool interface. Agents receive tools, never database handles.

This is the key architectural constraint of the project: an agent cannot compute a
covenant ratio, it can only call `get_covenant_status()`. That single rule is what
actually delivers "use deterministic code for financial arithmetic" -- the model
structures and explains, the engine computes.

Person 1 implements `Toolset` over the real engine; Person 2 codes against the Protocol
from day one. `FixtureToolset` in `backend/tools/fixtures.py` satisfies the same shape,
so nothing downstream knows or cares which one it has.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.tools.engine import EngineToolset as EngineToolset
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
    VarianceBridge,
)

# Hard row caps. A tool may return fewer rows; it may never return more.
MAX_ROWS = 25
DEFAULT_TOP_N = 10


class ToolError(RuntimeError):
    """A tool failed. Recorded on the `AgentRun`, surfaced as degraded, never swallowed."""


class ToolNotAllowed(ToolError):
    """An agent asked for a tool outside its allowlist. This is a bug, not a refusal."""


@runtime_checkable
class Toolset(Protocol):
    """Frozen signatures. Person 1 writes the bodies; both review a change to this file."""

    # --- shared position -------------------------------------------------------------

    async def get_liquidity_position(self) -> LiquidityPosition:
        """Cash today, the 13-week minimum, the floor, revolver headroom, runway."""
        ...

    async def get_covenant_status(self) -> CovenantStatus:
        """Every covenant with observed ratio, threshold and headroom, already computed."""
        ...

    async def get_policy_constraints(self) -> PolicyConstraints:
        """The versioned TreasuryPolicy thresholds. Constraints come from config, not code."""
        ...

    async def get_capability_manifest(self) -> CapabilityManifest:
        """Which sources this tenant has. The Commander plans -- and skips -- against this."""
        ...

    # --- forecast and variance -------------------------------------------------------

    async def get_forecast_summary(self, version_id: str | None = None) -> ForecastSummary:
        """The 13-week closing-cash series for one version. The engine computes it."""
        ...

    async def list_driver_assumptions(
        self, *, top_n: int = DEFAULT_TOP_N, stale_only: bool = False
    ) -> DriverAssumptions:
        """Drivers behind the forecast, with staleness already determined."""
        ...

    async def get_variance_bridge(
        self, *, week_ending: str | None = None, top_n: int = DEFAULT_TOP_N
    ) -> VarianceBridge:
        """Plan versus actual by category, material items flagged, deltas already netted."""
        ...

    async def get_forecast_error_percentiles(
        self, *, horizon_weeks: int
    ) -> ForecastErrorPercentiles:
        """Measured forecast error. The stress test is calibrated from this, not invented."""
        ...

    # --- receivables -----------------------------------------------------------------

    async def rank_collection_opportunities(
        self, *, top_n: int = DEFAULT_TOP_N
    ) -> CollectionOpportunities:
        """Ranked, probability-weighted rows from empirical collection curves."""
        ...

    async def get_ar_aging_summary(self) -> ArAgingSummary:
        """Aging buckets with totals. Open AR is not the same thing as collectible AR."""
        ...

    # --- payables and supplier risk --------------------------------------------------

    async def rank_deferral_candidates(
        self, *, top_n: int = DEFAULT_TOP_N, max_delay_days: int = 30
    ) -> DeferralCandidates:
        """Deferrable payables with the early-pay discount forgone already priced.

        Protected payment classes are returned marked, never silently omitted -- the AP
        agent has to be able to see that payroll exists and is off limits.
        """
        ...

    async def get_supplier_risk_profile(self, *, supplier: str) -> SupplierRiskProfile:
        """Concentration, sole-source status, late payments, disputes, credit hold."""
        ...

    # --- dodo ------------------------------------------------------------------------

    async def get_dodo_decline_breakdown(
        self, *, top_n: int = DEFAULT_TOP_N
    ) -> DodoDeclineBreakdown:
        """Declines by code under Dodo's documented soft/hard taxonomy, with recoverability."""
        ...

    # --- evidence --------------------------------------------------------------------

    async def resolve_evidence(self, *, reference: str) -> EvidenceRow:
        """Resolve a citation to a real row, or return `resolved=False`.

        The evidence validator calls this. An unresolvable reference means the finding is
        rejected, not surfaced.
        """
        ...
