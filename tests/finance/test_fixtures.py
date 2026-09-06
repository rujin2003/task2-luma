"""Fixture JSON must stay schema-valid so Person 2 can code against them."""

from __future__ import annotations

from typing import Any

from backend.contracts import (
    AccuracyStatDTO,
    BankRecon,
    CashPosition,
    CollectionOpportunity,
    Constraint,
    CovenantStatus,
    DebtCapacity,
    DeferralCandidate,
    DodoMetrics,
    ForecastGrid,
    VarianceBridge,
)


def test_cash_position_fixture(fixture_json: Any) -> None:
    CashPosition.model_validate(fixture_json("get_cash_position.json"))


def test_forecast_fixture(fixture_json: Any) -> None:
    ForecastGrid.model_validate(fixture_json("get_forecast.json"))


def test_variance_fixture(fixture_json: Any) -> None:
    VarianceBridge.model_validate(fixture_json("get_variance_bridge.json"))


def test_accuracy_fixture(fixture_json: Any) -> None:
    for row in fixture_json("get_accuracy_stats.json"):
        AccuracyStatDTO.model_validate(row)


def test_covenant_fixture(fixture_json: Any) -> None:
    for row in fixture_json("get_covenant_status.json"):
        CovenantStatus.model_validate(row)


def test_debt_fixture(fixture_json: Any) -> None:
    for row in fixture_json("get_debt_capacity.json"):
        DebtCapacity.model_validate(row)


def test_bank_recon_fixture(fixture_json: Any) -> None:
    BankRecon.model_validate(fixture_json("get_bank_reconciliation.json"))


def test_collections_fixture(fixture_json: Any) -> None:
    for row in fixture_json("rank_collection_opportunities.json"):
        CollectionOpportunity.model_validate(row)


def test_deferral_fixture(fixture_json: Any) -> None:
    for row in fixture_json("get_deferral_candidates.json"):
        DeferralCandidate.model_validate(row)


def test_dodo_fixture(fixture_json: Any) -> None:
    DodoMetrics.model_validate(fixture_json("get_dodo_metrics.json"))


def test_constraints_fixture(fixture_json: Any) -> None:
    for row in fixture_json("validate_constraints.json"):
        Constraint.model_validate(row)
