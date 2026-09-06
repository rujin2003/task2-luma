"""Derive treasury metrics from normalized Dodo events.

Recovery rates come from the documented soft/hard decline taxonomy plus a
profile-supplied soft-recovery basis points — never an invented percentage.
When Dodo is unavailable, return the last-known snapshot with `degraded=True`.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.contracts.common import MoneyDTO
from backend.contracts.dodo import DodoMetrics
from backend.integrations.dodo.normalize import PaymentEvent, PayoutEvent
from backend.integrations.dodo.taxonomy import DeclineKind, classify_decline, recovery_bps


@dataclass(frozen=True, slots=True)
class MetricsInputs:
    currency: str
    payments: tuple[PaymentEvent, ...]
    payouts: tuple[PayoutEvent, ...] = ()
    dispute_reserve_minor: int = 0
    soft_recovery_bps: int = 7200
    payout_lag_days: int = 2
    baseline_success_rate_bps: int = 9400
    last_known: DodoMetrics | None = None


def _money(amount_minor: int, currency: str) -> MoneyDTO:
    return MoneyDTO(amount=amount_minor, currency=currency)


def _is_success(status: str) -> bool:
    return status.casefold() in {"succeeded", "successful", "paid", "captured"}


def _is_failed(status: str) -> bool:
    return status.casefold() in {"failed", "declined", "canceled", "cancelled"}


def compute_metrics(inputs: MetricsInputs) -> DodoMetrics:
    """Aggregate expected / at-risk / recoverable collections from payment events."""
    currency = inputs.currency
    successes = [p for p in inputs.payments if _is_success(p.status)]
    failures = [p for p in inputs.payments if _is_failed(p.status)]
    total = len(successes) + len(failures)

    expected = sum(p.amount_minor for p in successes)
    at_risk = 0
    recoverable = 0
    for payment in failures:
        at_risk += payment.amount_minor
        if payment.failure_code is None:
            continue
        try:
            kind = classify_decline(payment.failure_code)
        except ValueError:
            continue
        if kind is DeclineKind.SOFT:
            recoverable += (
                payment.amount_minor
                * recovery_bps(payment.failure_code, soft_recovery_bps=inputs.soft_recovery_bps)
            ) // 10_000

    success_rate_bps = 0 if total == 0 else (10_000 * len(successes)) // total
    delta = success_rate_bps - inputs.baseline_success_rate_bps

    return DodoMetrics(
        expected_collections=_money(expected, currency),
        at_risk=_money(at_risk, currency),
        recoverable=_money(recoverable, currency),
        success_rate_bps=success_rate_bps,
        success_rate_delta_bps=delta,
        payout_lag_days=inputs.payout_lag_days,
        dispute_reserve=_money(max(0, inputs.dispute_reserve_minor), currency),
        degraded=False,
        degraded_reason=None,
    )


def degrade_metrics(
    last_known: DodoMetrics | None,
    *,
    reason: str,
    currency: str = "USD",
) -> DodoMetrics:
    """Last-known forecast with reduced confidence when Dodo is down."""
    if last_known is not None:
        return last_known.model_copy(update={"degraded": True, "degraded_reason": reason})
    zero = _money(0, currency)
    return DodoMetrics(
        expected_collections=zero,
        at_risk=zero,
        recoverable=zero,
        success_rate_bps=0,
        success_rate_delta_bps=0,
        payout_lag_days=0,
        dispute_reserve=zero,
        degraded=True,
        degraded_reason=reason,
    )


__all__ = ["MetricsInputs", "compute_metrics", "degrade_metrics"]
