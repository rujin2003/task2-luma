"""Per-role tool allowlists, and the scope an agent actually runs inside.

An agent gets a scoped view, not the whole ledger. The allowlist is the mechanism: the
Supplier Risk agent cannot pull the AR aging, and the AP agent cannot read Dodo declines.
Asking for a tool outside the list is a wiring bug, so it raises rather than refusing
politely -- a silent empty result would be worse.

`ScopedToolset` also collects, per run, every reference the tools actually returned. The
evidence validator checks findings against that set, which is what stops a model citing a
plausible-looking invoice number it never saw.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from backend.contracts.agent import AgentRole, ToolCall
from backend.tools.results import ToolPayload
from backend.tools.toolset import ToolError, ToolNotAllowed, Toolset

ALL_TOOLS: frozenset[str] = frozenset(
    name for name in dir(Toolset) if not name.startswith("_") and callable(getattr(Toolset, name))
)

# Tools every agent may call: where we stand, and what the rules are.
_SHARED = frozenset(
    {
        "get_liquidity_position",
        "get_policy_constraints",
        "resolve_evidence",
    }
)

TOOL_ALLOWLIST: dict[AgentRole, frozenset[str]] = {
    AgentRole.FORECAST: _SHARED | {"get_forecast_summary", "list_driver_assumptions"},
    AgentRole.VARIANCE: _SHARED
    | {"get_variance_bridge", "get_forecast_summary", "list_driver_assumptions"},
    AgentRole.AR_COLLECTIONS: _SHARED
    | {"rank_collection_opportunities", "get_ar_aging_summary"},
    AgentRole.AP_OPTIMIZATION: _SHARED | {"rank_deferral_candidates"},
    AgentRole.SUPPLIER_RISK: _SHARED
    | {"get_supplier_risk_profile", "rank_deferral_candidates"},
    AgentRole.DODO_REVENUE: _SHARED | {"get_dodo_decline_breakdown"},
    AgentRole.COMMANDER: _SHARED | {"get_capability_manifest", "get_covenant_status"},
    AgentRole.CONFLICT_RESOLUTION: _SHARED,
    AgentRole.STRESS_TEST: _SHARED
    | {"get_forecast_error_percentiles", "get_forecast_summary"},
    AgentRole.COVENANT_EXPLAINER: _SHARED | {"get_covenant_status"},
    AgentRole.CARTOGRAPHER: frozenset({"get_capability_manifest"}),
}


def tools_for(role: AgentRole) -> frozenset[str]:
    return TOOL_ALLOWLIST.get(role, _SHARED)


class ScopedToolset:
    """One agent's view of the tool layer, for the duration of one run."""

    def __init__(self, toolset: Toolset, role: AgentRole) -> None:
        self._toolset = toolset
        self.role = role
        self.allowed = tools_for(role)
        self.calls: list[ToolCall] = []
        self.references: set[str] = set()

    async def call(self, tool: str, /, **arguments: Any) -> ToolPayload:
        if tool not in ALL_TOOLS:
            raise ToolError(f"no such tool: {tool}")
        if tool not in self.allowed:
            raise ToolNotAllowed(f"{self.role.value} may not call {tool}")

        started_at = datetime.now(UTC)
        began = time.perf_counter()
        try:
            payload: ToolPayload = await getattr(self._toolset, tool)(**arguments)
        except Exception as exc:
            self._record(tool, arguments, started_at, began, error=str(exc))
            raise ToolError(f"{tool} failed: {exc}") from exc

        self.references.update(payload.references)
        self._record(tool, arguments, started_at, began, payload=payload)
        return payload

    def _record(
        self,
        tool: str,
        arguments: dict[str, Any],
        started_at: datetime,
        began: float,
        *,
        payload: ToolPayload | None = None,
        error: str | None = None,
    ) -> None:
        truncation = payload.truncation if payload else None
        self.calls.append(
            ToolCall(
                tool=tool,
                arguments=dict(arguments),
                started_at=started_at,
                duration_ms=int((time.perf_counter() - began) * 1000),
                row_count=truncation.showing if truncation else None,
                truncated_from=truncation.of if truncation else None,
                error=error,
            )
        )
