from __future__ import annotations

import inspect

import pytest

from backend.tools.signatures import (
    get_accuracy_stats,
    get_bank_reconciliation,
    get_cash_position,
    get_covenant_status,
    get_debt_capacity,
    get_deferral_candidates,
    get_dodo_metrics,
    get_forecast,
    get_policy,
    get_variance_bridge,
    rank_collection_opportunities,
    validate_constraints,
)

STUBS = [
    get_policy,
    get_cash_position,
    get_forecast,
    get_variance_bridge,
    get_accuracy_stats,
    get_covenant_status,
    get_debt_capacity,
    get_bank_reconciliation,
    rank_collection_opportunities,
    get_deferral_candidates,
    get_dodo_metrics,
    validate_constraints,
]


def _minimal_kwargs(fn: object) -> dict[str, object]:
    params = inspect.signature(fn).parameters  # type: ignore[arg-type]
    extras: dict[str, object] = {}
    if "account_ref" in params:
        extras["account_ref"] = "op-4471"
    if "as_of" in params:
        extras["as_of"] = None
    if "kind" in params:
        extras["kind"] = "forecast_vs_actual"
    if "action" in params:
        extras["action"] = "revolver_draw"
    if "amount_minor" in params:
        extras["amount_minor"] = 1
    if "currency" in params:
        extras["currency"] = "USD"
    return extras


@pytest.mark.parametrize("fn", STUBS, ids=lambda f: f.__name__)
def test_tool_is_stubbed(fn: object) -> None:
    assert callable(fn)
    kwargs = {"tenant_id": "novatech", **_minimal_kwargs(fn)}
    with pytest.raises(NotImplementedError):
        fn(**kwargs)  # type: ignore[operator]
