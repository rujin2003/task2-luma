from __future__ import annotations

from datetime import UTC, date, datetime

from backend.contracts import (
    AgentFinding,
    CashPosition,
    Evidence,
    ForecastGrid,
    MoneyDTO,
    VarianceBridge,
    WorklistItem,
)
from backend.finance import Provenance


def _prov() -> Provenance:
    return Provenance(
        source_system="seed",
        source_table="bank_accounts",
        source_pk="op-4471",
        field="balance_minor",
        as_of=datetime(2026, 8, 30, tzinfo=UTC),
        retrieved_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


def test_money_dto_from_money() -> None:
    dto = MoneyDTO(amount=2380000000, currency="USD")
    assert dto.to_money().to_major_string() == "23800000.00"


def test_agent_finding_schema() -> None:
    finding = AgentFinding(
        agent="variance",
        status="complete",
        finding={"delta_minor": -190000000},
        evidence=(
            Evidence(
                source="ar_ledger",
                reference="customer_A_invoice_1832",
                provenance=_prov(),
            ),
        ),
        risks=("Customer A slipped $900K to W2",),
        recommended_actions=("Call re: overdue invoice 1832",),
    )
    assert finding.status == "complete"
    assert finding.evidence[0].reference == "customer_A_invoice_1832"


def test_cash_position_and_forecast_and_worklist_are_schema_valid() -> None:
    usd = MoneyDTO(amount=2380000000, currency="USD")
    cash = CashPosition(
        current_cash=usd,
        restricted_cash=MoneyDTO(amount=0, currency="USD"),
        unrestricted_cash=usd,
        pending_in=MoneyDTO(amount=0, currency="USD"),
        pending_out=MoneyDTO(amount=0, currency="USD"),
        undrawn_revolver=MoneyDTO(amount=500000000, currency="USD"),
        available_liquidity=MoneyDTO(amount=2880000000, currency="USD"),
        as_of=datetime(2026, 8, 30, tzinfo=UTC),
        provenance=(_prov(),),
    )
    assert cash.unrestricted_cash.amount == 2380000000

    grid = ForecastGrid(
        version_id="fv-0001",
        as_of=datetime(2026, 9, 1, tzinfo=UTC),
        opening_cash=usd,
        weeks=(date(2026, 9, 7),),
        lines=(),
        closing_cash_by_week=(usd,),
        available_liquidity_by_week=(usd,),
    )
    assert len(grid.weeks) == 1

    bridge = VarianceBridge(
        kind="forecast_vs_actual",
        week_ending=date(2026, 8, 30),
        rows=(),
        closing_variance=MoneyDTO(amount=-190000000, currency="USD"),
    )
    assert bridge.closing_variance.amount == -190000000

    item = WorklistItem(
        owner="A. Rivera",
        action="Call re: overdue invoice 1832",
        counterparty="Customer A",
        document_ref="INV-1832",
        amount=MoneyDTO(amount=90000000, currency="USD"),
        due_date=date(2026, 9, 8),
        status="open",
    )
    assert item.document_ref == "INV-1832"
