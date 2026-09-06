from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlmodel import Session, SQLModel, create_engine

from backend.seed.generator import SeedConfig, seed
from backend.tools.engine import EngineToolset
from backend.tools.results import (
    ArAgingSummary,
    CapabilityManifest,
    CollectionOpportunities,
    CovenantStatus,
    DeferralCandidates,
    DodoDeclineBreakdown,
    DriverAssumptions,
    ForecastErrorPercentiles,
    ForecastSummary,
    LiquidityPosition,
    PolicyConstraints,
    SupplierRiskProfile,
    VarianceBridge,
)


@pytest.fixture(scope="module")
def toolset() -> Iterator[EngineToolset]:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seeded = seed(session, SeedConfig(seed=42))
        session.commit()
        yield EngineToolset(session, as_of=seeded.as_of)
    engine.dispose()


@pytest.mark.asyncio
async def test_every_tool_returns_its_contract_payload(toolset: EngineToolset) -> None:
    calls = [
        (toolset.get_liquidity_position(), LiquidityPosition),
        (toolset.get_covenant_status(), CovenantStatus),
        (toolset.get_policy_constraints(), PolicyConstraints),
        (toolset.get_capability_manifest(), CapabilityManifest),
        (toolset.get_forecast_summary(), ForecastSummary),
        (toolset.list_driver_assumptions(), DriverAssumptions),
        (toolset.get_variance_bridge(), VarianceBridge),
        (
            toolset.get_forecast_error_percentiles(horizon_weeks=1),
            ForecastErrorPercentiles,
        ),
        (toolset.rank_collection_opportunities(), CollectionOpportunities),
        (toolset.get_ar_aging_summary(), ArAgingSummary),
        (toolset.rank_deferral_candidates(), DeferralCandidates),
        (
            toolset.get_supplier_risk_profile(supplier="Quantum Instruments"),
            SupplierRiskProfile,
        ),
        (toolset.get_dodo_decline_breakdown(), DodoDeclineBreakdown),
    ]
    for call, payload_type in calls:
        payload = await call
        assert isinstance(payload, payload_type)
        assert payload_type.model_validate(payload.model_dump()) == payload


@pytest.mark.asyncio
async def test_covenants_and_collection_evidence_are_real(toolset: EngineToolset) -> None:
    covenants = await toolset.get_covenant_status()
    assert covenants.covenants

    opportunities = await toolset.rank_collection_opportunities(top_n=5)
    assert opportunities.rows
    for row in opportunities.rows:
        evidence = await toolset.resolve_evidence(reference=row.reference)
        assert evidence.resolved
        assert evidence.reference == row.reference


@pytest.mark.asyncio
async def test_payroll_deferrals_remain_visible_and_protected(
    toolset: EngineToolset,
) -> None:
    candidates = await toolset.rank_deferral_candidates(top_n=25)
    payroll = [row for row in candidates.rows if row.payment_class == "payroll"]
    assert payroll
    assert all(row.protected and row.max_delay_days == 0 for row in payroll)
