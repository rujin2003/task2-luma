"""Read-only Dodo Payments integration boundary.

Provider-specific HTTP, webhook verification and decline taxonomy stay here.
The rest of the system sees `PaymentEvent` and `DodoMetrics` only.
"""

from backend.integrations.dodo.client import (
    LIVE_BASE_URL,
    TEST_BASE_URL,
    DodoClient,
    DodoUnavailable,
    FakeDodoClient,
)
from backend.integrations.dodo.metrics import MetricsInputs, compute_metrics, degrade_metrics
from backend.integrations.dodo.normalize import (
    CashTiming,
    LedgerEntry,
    PaymentEvent,
    PayoutEvent,
    expected_settlement_date,
    normalize_ledger_entry,
    normalize_payment,
    normalize_payout,
    resolve_cash_timing,
)
from backend.integrations.dodo.taxonomy import DeclineKind, classify_decline, recovery_bps
from backend.integrations.dodo.webhooks import (
    VerifiedWebhook,
    WebhookStore,
    WebhookVerificationError,
    receive_webhook,
    sign_payload,
    verify_signature,
)

__all__ = [
    "LIVE_BASE_URL",
    "TEST_BASE_URL",
    "CashTiming",
    "DeclineKind",
    "DodoClient",
    "DodoUnavailable",
    "FakeDodoClient",
    "LedgerEntry",
    "MetricsInputs",
    "PaymentEvent",
    "PayoutEvent",
    "VerifiedWebhook",
    "WebhookStore",
    "WebhookVerificationError",
    "classify_decline",
    "compute_metrics",
    "degrade_metrics",
    "expected_settlement_date",
    "normalize_ledger_entry",
    "normalize_payment",
    "normalize_payout",
    "receive_webhook",
    "recovery_bps",
    "resolve_cash_timing",
    "sign_payload",
    "verify_signature",
]
