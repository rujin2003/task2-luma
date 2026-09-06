"""Failure-injection helpers for Person 1 degradation paths.

Dodo down and incomplete bank data must degrade rather than crash.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.contracts.dodo import DodoMetrics
from backend.integrations.dodo.client import DodoUnavailable, FakeDodoClient
from backend.integrations.dodo.metrics import degrade_metrics


@dataclass(frozen=True, slots=True)
class DegradedBankView:
    complete: bool
    unrestricted_cash_minor: int | None
    reason: str | None


async def dodo_down_metrics(last_known: DodoMetrics | None) -> DodoMetrics:
    client = FakeDodoClient(unavailable=True)
    try:
        await client.list_payments()
    except DodoUnavailable as exc:
        return degrade_metrics(last_known, reason=str(exc))
    raise AssertionError("FakeDodoClient(unavailable=True) must raise")


def incomplete_bank_data(
    *,
    balances_present: bool,
    unrestricted_cash_minor: int | None,
) -> DegradedBankView:
    if not balances_present or unrestricted_cash_minor is None:
        return DegradedBankView(
            complete=False,
            unrestricted_cash_minor=None,
            reason="bank balances incomplete; recommendation requires human review",
        )
    return DegradedBankView(
        complete=True,
        unrestricted_cash_minor=unrestricted_cash_minor,
        reason=None,
    )


__all__ = ["DegradedBankView", "dodo_down_metrics", "incomplete_bank_data"]
