"""The tool layer's rules, asserted.

The architectural constraint under test: an agent gets pre-computed decisions inside a
scoped allowlist, with truncation reported and every citable reference accounted for.
"""

from __future__ import annotations

import inspect

import pytest

from backend.contracts import AgentRole
from backend.tools.fixtures import FixtureToolset
from backend.tools.registry import ALL_TOOLS, TOOL_ALLOWLIST, ScopedToolset, tools_for
from backend.tools.toolset import MAX_ROWS, EngineToolset, ToolError, ToolNotAllowed, Toolset


@pytest.fixture
def toolset() -> FixtureToolset:
    return FixtureToolset()


def test_the_fixture_toolset_satisfies_the_frozen_protocol(toolset: FixtureToolset) -> None:
    assert isinstance(toolset, Toolset)
    for tool in ALL_TOOLS:
        assert hasattr(toolset, tool), f"FixtureToolset is missing {tool}"


def test_the_engine_toolset_requires_a_database_session() -> None:
    signature = inspect.signature(EngineToolset)
    assert signature.parameters["session"].default is inspect.Parameter.empty


def test_every_role_has_an_allowlist_of_real_tools() -> None:
    for role in AgentRole:
        assert role in TOOL_ALLOWLIST, f"{role} has no allowlist"
        assert tools_for(role) <= ALL_TOOLS


async def test_an_agent_cannot_reach_outside_its_scope(toolset: FixtureToolset) -> None:
    """Supplier Risk has no business reading the AR aging."""
    scoped = ScopedToolset(toolset, AgentRole.SUPPLIER_RISK)
    with pytest.raises(ToolNotAllowed):
        await scoped.call("get_ar_aging_summary")


async def test_dodo_declines_are_out_of_scope_for_the_ap_agent(toolset: FixtureToolset) -> None:
    scoped = ScopedToolset(toolset, AgentRole.AP_OPTIMIZATION)
    with pytest.raises(ToolNotAllowed):
        await scoped.call("get_dodo_decline_breakdown")


async def test_an_unknown_tool_is_an_error_not_a_refusal(toolset: FixtureToolset) -> None:
    scoped = ScopedToolset(toolset, AgentRole.COMMANDER)
    with pytest.raises(ToolError):
        await scoped.call("get_the_answer")


async def test_a_call_is_recorded_with_its_references(toolset: FixtureToolset) -> None:
    scoped = ScopedToolset(toolset, AgentRole.AR_COLLECTIONS)
    payload = await scoped.call("rank_collection_opportunities", top_n=3)

    assert len(scoped.calls) == 1
    call = scoped.calls[0]
    assert call.tool == "rank_collection_opportunities"
    assert call.error is None
    assert scoped.references == set(payload.references)
    assert "ar_ledger:INV-10482#amount_due" in scoped.references


async def test_truncation_is_reported_not_silent(toolset: FixtureToolset) -> None:
    scoped = ScopedToolset(toolset, AgentRole.AR_COLLECTIONS)
    payload = await scoped.call("rank_collection_opportunities", top_n=2)

    assert payload.truncation is not None
    assert payload.truncation.display() == "showing 2 of 5"
    assert scoped.calls[0].row_count == 2
    assert scoped.calls[0].truncated_from == 5


async def test_row_caps_are_hard(toolset: FixtureToolset) -> None:
    """A caller asking for a thousand rows gets the cap, not a thousand rows."""
    payload = await toolset.rank_collection_opportunities(top_n=1000)
    assert len(payload.rows) <= MAX_ROWS


async def test_collection_rows_are_probability_weighted(toolset: FixtureToolset) -> None:
    """Open AR is not collectible AR: the expected total must be the smaller number."""
    payload = await toolset.rank_collection_opportunities(top_n=10)
    assert payload.total_expected < payload.total_open
    for row in payload.rows:
        assert row.expected_amount <= row.amount


async def test_protected_payables_are_returned_marked_not_hidden(
    toolset: FixtureToolset,
) -> None:
    """The AP agent has to be able to see that payroll exists and is off limits."""
    payload = await toolset.rank_deferral_candidates(top_n=10)
    protected = [row for row in payload.rows if row.protected]
    assert {row.payment_class for row in protected} == {"payroll", "tax"}
    assert all(row.amount.minor_units > 0 for row in protected)


async def test_deferrable_total_excludes_protected_rows(toolset: FixtureToolset) -> None:
    payload = await toolset.rank_deferral_candidates(top_n=10)
    deferrable = [row for row in payload.rows if not row.protected]
    assert payload.total_deferrable.minor_units == sum(row.amount.minor_units for row in deferrable)


async def test_stale_drivers_can_be_asked_for_directly(toolset: FixtureToolset) -> None:
    payload = await toolset.list_driver_assumptions(stale_only=True)
    assert payload.rows
    assert all(row.stale for row in payload.rows)


async def test_dodo_recovery_follows_the_soft_hard_taxonomy(toolset: FixtureToolset) -> None:
    """A hard decline is not recoverable. The rate is documented, not invented."""
    payload = await toolset.get_dodo_decline_breakdown(top_n=10)
    for row in payload.rows:
        if row.kind == "hard":
            assert row.recoverable_amount.is_zero()
            assert row.retry_window_days is None
        else:
            assert row.retry_window_days is not None
    assert payload.recoverable_total < payload.at_risk_total


async def test_stress_calibration_comes_from_measured_error(toolset: FixtureToolset) -> None:
    payload = await toolset.get_forecast_error_percentiles(horizon_weeks=6)
    assert payload.percentiles
    for point in payload.percentiles:
        assert point.horizon_weeks == 6
        assert point.p90_pct > point.p50_pct

    with pytest.raises(ToolError):
        await toolset.get_forecast_error_percentiles(horizon_weeks=2)


async def test_the_liquidity_position_knows_it_is_breached(toolset: FixtureToolset) -> None:
    position = await toolset.get_liquidity_position()
    assert position.breaches_floor
    assert position.min_cash_week == 6


async def test_evidence_resolves_or_says_it_did_not(toolset: FixtureToolset) -> None:
    resolved = await toolset.resolve_evidence(reference="ar_ledger:INV-10482#amount_due")
    assert resolved.resolved
    assert "10482" in resolved.excerpt

    invented = await toolset.resolve_evidence(reference="ar_ledger:INV-99999#amount_due")
    assert not invented.resolved
