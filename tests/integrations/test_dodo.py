from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from backend.integrations.dodo.client import DodoUnavailable, FakeDodoClient
from backend.integrations.dodo.metrics import MetricsInputs, compute_metrics, degrade_metrics
from backend.integrations.dodo.normalize import (
    expected_settlement_date,
    normalize_payment,
    resolve_cash_timing,
)
from backend.integrations.dodo.webhooks import (
    WebhookStore,
    WebhookVerificationError,
    receive_webhook,
    sign_payload,
)


def test_fake_client_lists_and_degrades() -> None:
    client = FakeDodoClient(
        payments=[
            {
                "payment_id": "pay_1",
                "status": "succeeded",
                "amount": 1000,
                "currency": "usd",
                "created_at": "2026-09-01T00:00:00Z",
            }
        ]
    )

    async def _run() -> None:
        rows = await client.list_payments()
        assert len(rows) == 1
        down = FakeDodoClient(unavailable=True)
        with pytest.raises(DodoUnavailable):
            await down.list_payments()

    import asyncio

    asyncio.run(_run())


def test_payout_lag_is_business_days() -> None:
    paid = datetime(2026, 9, 4, tzinfo=UTC)  # Friday
    settle = expected_settlement_date(paid, lag_business_days=2)
    assert settle.isoformat() == "2026-09-08"  # skips weekend


def test_cash_timing_distinguishes_payment_from_settlement() -> None:
    payment = normalize_payment(
        {
            "payment_id": "pay_1",
            "status": "succeeded",
            "amount_minor": 500_00,
            "currency": "USD",
            "created_at": "2026-09-01T12:00:00Z",
        }
    )
    timing = resolve_cash_timing(payment, ledger=[], payouts=[], lag_business_days=3)
    assert timing.stage == "payment"
    assert timing.settlement_date is not None
    assert timing.settlement_date > payment.occurred_at.date()


def test_metrics_use_soft_decline_taxonomy() -> None:
    payments = (
        normalize_payment(
            {
                "payment_id": "ok",
                "status": "succeeded",
                "amount_minor": 1_000_00,
                "currency": "USD",
                "created_at": "2026-09-01T00:00:00Z",
            }
        ),
        normalize_payment(
            {
                "payment_id": "soft",
                "status": "failed",
                "amount_minor": 400_00,
                "currency": "USD",
                "failure_code": "insufficient_funds",
                "created_at": "2026-09-01T00:00:00Z",
            }
        ),
        normalize_payment(
            {
                "payment_id": "hard",
                "status": "failed",
                "amount_minor": 200_00,
                "currency": "USD",
                "failure_code": "fraudulent",
                "created_at": "2026-09-01T00:00:00Z",
            }
        ),
    )
    metrics = compute_metrics(
        MetricsInputs(
            currency="USD",
            payments=payments,
            soft_recovery_bps=7200,
            baseline_success_rate_bps=9400,
        )
    )
    assert metrics.expected_collections.amount == 1_000_00
    assert metrics.at_risk.amount == 600_00
    assert metrics.recoverable.amount == 288_00  # 72% of soft only
    assert metrics.degraded is False


def test_degraded_metrics_preserve_last_known() -> None:
    payments = (
        normalize_payment(
            {
                "payment_id": "ok",
                "status": "succeeded",
                "amount_minor": 50_00,
                "currency": "USD",
                "created_at": "2026-09-01T00:00:00Z",
            }
        ),
    )
    known = compute_metrics(MetricsInputs(currency="USD", payments=payments))
    degraded = degrade_metrics(known, reason="injected Dodo outage")
    assert degraded.degraded is True
    assert degraded.expected_collections.amount == 50_00
    assert degrade_metrics(None, reason="cold start").degraded is True


def test_webhook_signature_and_idempotency() -> None:
    secret = "whsec_dGVzdHNlY3JldA=="
    body = json.dumps(
        {"type": "payment.succeeded", "data": {"payment_id": "pay_1", "status": "succeeded"}}
    )
    webhook_id = "msg_1"
    timestamp = "1694000000"
    signature = sign_payload(secret=secret, webhook_id=webhook_id, timestamp=timestamp, body=body)
    store = WebhookStore()
    first = receive_webhook(
        secret=secret,
        headers={
            "webhook-id": webhook_id,
            "webhook-timestamp": timestamp,
            "webhook-signature": signature,
        },
        body=body,
        store=store,
        now=1694000000,
    )
    second = receive_webhook(
        secret=secret,
        headers={
            "webhook-id": webhook_id,
            "webhook-timestamp": timestamp,
            "webhook-signature": signature,
        },
        body=body,
        store=store,
        now=1694000000,
    )
    assert first.duplicate is False
    assert second.duplicate is True
    assert first.event_type == "payment.succeeded"

    with pytest.raises(WebhookVerificationError):
        receive_webhook(
            secret=secret,
            headers={
                "webhook-id": webhook_id,
                "webhook-timestamp": timestamp,
                "webhook-signature": "v1,deadbeef",
            },
            body=body,
            store=WebhookStore(),
            now=1694000000,
        )
