from __future__ import annotations

import pytest

from backend.finance.failures import dodo_down_metrics, incomplete_bank_data
from backend.integrations.dodo.metrics import MetricsInputs, compute_metrics
from backend.integrations.dodo.normalize import normalize_payment


@pytest.mark.asyncio
async def test_dodo_down_degrades_last_known() -> None:
    known = compute_metrics(
        MetricsInputs(
            currency="USD",
            payments=(
                normalize_payment(
                    {
                        "payment_id": "p1",
                        "status": "succeeded",
                        "amount_minor": 10_00,
                        "currency": "USD",
                        "created_at": "2026-09-01T00:00:00Z",
                    }
                ),
            ),
        )
    )
    degraded = await dodo_down_metrics(known)
    assert degraded.degraded is True
    assert "outage" in (degraded.degraded_reason or "").lower()
    assert degraded.expected_collections.amount == 10_00


def test_incomplete_bank_data_flags_human_review() -> None:
    view = incomplete_bank_data(balances_present=False, unrestricted_cash_minor=None)
    assert view.complete is False
    assert view.reason is not None
    assert "human review" in view.reason
    ok = incomplete_bank_data(balances_present=True, unrestricted_cash_minor=23_800_000_00)
    assert ok.complete is True
