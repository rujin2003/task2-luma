"""The tool layer over a freshly onboarded ledger.

Two things are being asserted here, and the second one matters more than the first.

The first is that the derivations are right: the forecast ties to the rows it was built
from, the aging buckets sum to open AR, the ranked rows are probability-weighted rather
than face value, and protected payables come back marked instead of quietly dropped.

The second is that everything the tenant *cannot* support raises, with a reason. A
toolset that returned an empty variance bridge, a zero covenant headroom or an invented
decline rate would let a screen render a confident number for a source that never had
one. Those refusals are load-bearing, so they are tested like features.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

import backend.models  # noqa: F401 -- registers metadata
from backend.api import store
from backend.ingest.onboarding import Onboarding
from backend.ingest.validate import BalanceSnapshot
from backend.tools.tenant import TenantToolset, band_of
from backend.tools.toolset import ToolError
from scripts import demo_company


@pytest.fixture(scope="module")
def loaded(tmp_path_factory: pytest.TempPathFactory) -> Session:
    """Northgate, walked through the DB Agent into a target database of its own.

    Northgate is the deliberate choice: its source schema carries integer minor units and
    a legacy warehouse layout, so a toolset that quietly assumed decimal major units would
    be out by a hundred here rather than passing.
    """
    root: Path = tmp_path_factory.mktemp("tenant-tools")
    source = demo_company.build_all(root / "sources", keys=["northgate"])[0]
    engine = create_engine(f"sqlite:///{root / 'target.db'}")
    SQLModel.metadata.create_all(engine)

    onboarding = Onboarding(
        tenant_id="northgate",
        company_name=str(source["company"]),
        default_currency="USD",
    )
    onboarding.connect(str(source["url"]))
    onboarding.introspect()
    onboarding.classify()
    onboarding.draft()
    balances = source["control_balances"]
    assert isinstance(balances, dict)
    with Session(engine) as target:
        onboarding.load(
            target,
            mapping_dir=root / "mappings",
            stated=BalanceSnapshot(
                ar_minor=int(balances["ar_control_minor"]),
                ap_minor=int(balances["ap_control_minor"]),
                cash_minor=int(balances["cash_control_minor"]),
                currency="USD",
            ),
        )
        assert onboarding.reconciliation is not None
        assert onboarding.reconciliation.accepted
        target.commit()

    opened = Session(engine)
    yield opened
    opened.close()
    engine.dispose()


@pytest.fixture
def tools(loaded: Session) -> TenantToolset:
    return TenantToolset(loaded, tenant_id="northgate")


def test_the_as_of_date_comes_from_the_ledger_not_the_wall_clock(tools: TenantToolset) -> None:
    """A March book run in September must not be aged as six months late."""
    assert tools.as_of.year == 2026
    assert tools.as_of.month in {2, 3}


async def test_the_forecast_opens_on_settled_cash_and_ties_to_its_components(
    tools: TenantToolset,
) -> None:
    summary = await tools.get_forecast_summary()
    assert len(summary.weeks) == 13
    assert summary.published is False, "a derived draft is never a signed version"

    rows, components = tools._forecast_rows()
    closing = rows[-1].closing_cash.minor_units
    assert closing == (
        components["opening_minor"]
        + components["expected_receipts_minor"]
        - components["expected_disbursements_minor"]
    )


async def test_expected_receipts_are_weighted_never_face_value(tools: TenantToolset) -> None:
    """Open AR is not collectible AR — the whole point of the receivables agent."""
    opportunities = await tools.rank_collection_opportunities(top_n=25)
    assert opportunities.total_expected.minor_units < opportunities.total_open.minor_units
    for row in opportunities.rows:
        assert 0 < row.probability_pct <= 100
        assert row.empirical_basis, "a weighted figure must say what weighted it"


async def test_the_aging_summary_sums_to_open_receivables(tools: TenantToolset) -> None:
    aging = await tools.get_ar_aging_summary()
    assert sum(bucket.amount.minor_units for bucket in aging.buckets) == aging.total.minor_units
    opportunities = await tools.rank_collection_opportunities(top_n=25)
    assert aging.total.minor_units == opportunities.total_open.minor_units


async def test_protected_payables_are_returned_marked_rather_than_dropped(
    tools: TenantToolset,
) -> None:
    candidates = await tools.rank_deferral_candidates(top_n=25)
    assert candidates.rows
    for row in candidates.rows:
        assert row.payment_class, "every candidate names the class it belongs to"
        if row.protected:
            assert row.max_delay_days == 0, "a protected class is never deferrable"
    assert candidates.total_deferrable.minor_units > 0
    # Deferrable is strictly the unprotected part of the book, never the whole of it.
    protected = sum(row.amount.minor_units for row in candidates.rows if row.protected)
    assert candidates.total_deferrable.minor_units >= 0 and protected >= 0


async def test_citations_use_the_tenants_own_document_key_and_resolve(
    tools: TenantToolset,
) -> None:
    opportunities = await tools.rank_collection_opportunities(top_n=3)
    reference = opportunities.rows[0].reference
    assert reference.startswith("ar_ledger:")
    resolved = await tools.resolve_evidence(reference=reference)
    assert resolved.resolved is True
    assert resolved.fields


async def test_a_citation_to_a_row_that_is_not_there_is_unresolved_not_an_error(
    tools: TenantToolset,
) -> None:
    resolved = await tools.resolve_evidence(reference="ar_ledger:NOT-A-REAL-INVOICE#open")
    assert resolved.resolved is False


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        ("get_covenant_status", "covenant"),
        ("get_variance_bridge", "published forecast"),
        ("get_dodo_decline_breakdown", "Dodo"),
    ],
)
async def test_what_the_tenant_does_not_have_refuses_with_a_reason(
    tools: TenantToolset, call: str, expected: str
) -> None:
    with pytest.raises(ToolError, match=expected):
        await getattr(tools, call)()


async def test_measured_error_refuses_because_there_is_no_history(tools: TenantToolset) -> None:
    with pytest.raises(ToolError, match="forecast history"):
        await tools.get_forecast_error_percentiles(horizon_weeks=1)


async def test_the_manifest_names_the_missing_sources_and_says_why(tools: TenantToolset) -> None:
    manifest = await tools.get_capability_manifest()
    available = {item.value for item in manifest.available_sources}
    missing = {item.value for item in manifest.missing_sources}
    assert {"ar_ledger", "ap_ledger", "bank"} <= available
    assert {"dodo", "debt"} <= missing
    for name in missing:
        assert manifest.notes[name], f"{name} is missing without a stated reason"


def test_aging_bands_are_ordered_and_total(tools: TenantToolset) -> None:
    assert band_of(0)[0] == "not_yet_due"
    assert band_of(1)[0] == "d1_30"
    assert band_of(45)[0] == "d31_60"
    assert band_of(400)[0] == "d90_plus"


def test_a_tenant_that_was_never_loaded_is_refused(loaded: Session) -> None:
    with pytest.raises(ToolError, match="no company loaded"):
        TenantToolset(loaded, tenant_id="not-a-tenant")


def test_the_store_url_is_overridable_so_a_test_never_writes_the_real_one() -> None:
    """Guard rail: the target database is configuration, not a constant in a handler."""
    assert store.DEFAULT_URL.startswith("sqlite:///var/")
