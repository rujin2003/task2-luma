"""Dodo webhook verification and idempotent intake.

Follows the Standard Webhooks specification used by Dodo Payments:
signed content is `{webhook-id}.{webhook-timestamp}.{raw_body}`, HMAC-SHA256
with the dashboard secret (optionally `whsec_`-prefixed base64).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any


class WebhookVerificationError(ValueError):
    """Signature, timestamp, or payload failed verification."""


@dataclass
class WebhookStore:
    """Idempotency store keyed by `webhook-id`. In-memory for tests; swap later."""

    _seen: set[str] = field(default_factory=set)

    def seen(self, webhook_id: str) -> bool:
        return webhook_id in self._seen

    def mark(self, webhook_id: str) -> None:
        self._seen.add(webhook_id)


def _secret_bytes(secret: str) -> bytes:
    raw = secret.strip()
    if raw.startswith("whsec_"):
        raw = raw[len("whsec_") :]
    try:
        return base64.b64decode(raw)
    except Exception:
        return secret.encode("utf-8")


def sign_payload(
    *,
    secret: str,
    webhook_id: str,
    timestamp: str,
    body: bytes | str,
) -> str:
    """Produce a `v1,<base64>` signature for tests and golden fixtures."""
    if isinstance(body, str):
        body = body.encode("utf-8")
    message = f"{webhook_id}.{timestamp}.".encode() + body
    digest = hmac.new(_secret_bytes(secret), message, hashlib.sha256).digest()
    return f"v1,{base64.b64encode(digest).decode('ascii')}"


def verify_signature(
    *,
    secret: str,
    webhook_id: str,
    timestamp: str,
    signature_header: str,
    body: bytes | str,
    tolerance_s: int = 300,
    now: int | None = None,
) -> None:
    """Raise `WebhookVerificationError` unless the Standard Webhooks signature matches."""
    if not webhook_id or not timestamp or not signature_header:
        raise WebhookVerificationError("missing webhook verification headers")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise WebhookVerificationError("invalid webhook-timestamp") from exc
    clock = int(time.time() if now is None else now)
    if abs(clock - ts) > tolerance_s:
        raise WebhookVerificationError("webhook timestamp outside tolerance")

    expected = sign_payload(secret=secret, webhook_id=webhook_id, timestamp=timestamp, body=body)
    candidates = [part.strip() for part in signature_header.split(" ")]
    if not any(hmac.compare_digest(expected, candidate) for candidate in candidates):
        # Also accept bare base64 without the v1, prefix that some relays strip.
        bare = expected.split(",", 1)[1]
        if not any(hmac.compare_digest(bare, candidate) for candidate in candidates):
            raise WebhookVerificationError("webhook signature mismatch")


@dataclass(frozen=True, slots=True)
class VerifiedWebhook:
    webhook_id: str
    timestamp: str
    event_type: str
    data: dict[str, Any]
    duplicate: bool


def receive_webhook(
    *,
    secret: str,
    headers: dict[str, str],
    body: bytes | str,
    store: WebhookStore,
    tolerance_s: int = 300,
    now: int | None = None,
) -> VerifiedWebhook:
    """Verify, parse, and idempotently accept one Dodo webhook delivery."""
    webhook_id = headers.get("webhook-id") or headers.get("Webhook-Id") or ""
    timestamp = headers.get("webhook-timestamp") or headers.get("Webhook-Timestamp") or ""
    signature = headers.get("webhook-signature") or headers.get("Webhook-Signature") or ""

    verify_signature(
        secret=secret,
        webhook_id=webhook_id,
        timestamp=timestamp,
        signature_header=signature,
        body=body,
        tolerance_s=tolerance_s,
        now=now,
    )

    raw = body if isinstance(body, str) else body.decode("utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WebhookVerificationError("webhook body is not JSON") from exc
    if not isinstance(payload, dict):
        raise WebhookVerificationError("webhook body must be a JSON object")

    event_type = str(payload.get("type") or payload.get("event_type") or "")
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise WebhookVerificationError("webhook data must be an object")
    if not event_type:
        raise WebhookVerificationError("webhook missing event type")

    duplicate = store.seen(webhook_id)
    if not duplicate:
        store.mark(webhook_id)
    return VerifiedWebhook(
        webhook_id=webhook_id,
        timestamp=timestamp,
        event_type=event_type,
        data=data,
        duplicate=duplicate,
    )


__all__ = [
    "VerifiedWebhook",
    "WebhookStore",
    "WebhookVerificationError",
    "receive_webhook",
    "sign_payload",
    "verify_signature",
]
