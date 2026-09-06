"""Read-only Dodo Payments client.

Nothing outside `backend/integrations/dodo/` imports this module's HTTP details.
The rest of the system sees `PaymentEvent` and derived metrics only.

Auth is Bearer; ingestion paths must use a read-only key. Write endpoints are
intentionally absent.
"""

from __future__ import annotations

from typing import Any

import httpx

TEST_BASE_URL = "https://test.dodopayments.com"
LIVE_BASE_URL = "https://live.dodopayments.com"


class DodoUnavailable(RuntimeError):
    """Dodo data cannot currently be read — degrade the forecast, do not crash."""


class DodoClient:
    """Minimal read-only boundary over the verified Dodo REST surface."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = TEST_BASE_URL,
        timeout_s: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _require_key(self) -> str:
        if not self.api_key:
            raise DodoUnavailable("Dodo payment reads are not configured")
        return self.api_key

    async def _get(self, path: str, **params: Any) -> Any:
        key = self._require_key()
        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        cleaned = {k: v for k, v in params.items() if v is not None}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                response = await client.get(
                    f"{self.base_url}{path}",
                    headers=headers,
                    params=cleaned,
                )
        except httpx.HTTPError as exc:
            raise DodoUnavailable(f"Dodo request failed: {exc}") from exc
        if response.status_code >= 500:
            raise DodoUnavailable(f"Dodo unavailable ({response.status_code})")
        if response.status_code == 401 or response.status_code == 403:
            raise DodoUnavailable("Dodo credentials rejected")
        if response.status_code >= 400:
            raise DodoUnavailable(f"Dodo client error ({response.status_code})")
        return response.json()

    async def list_payments(self, **filters: Any) -> list[dict[str, Any]]:
        payload = await self._get("/payments", **filters)
        return _items(payload)

    async def get_payment(self, payment_id: str) -> dict[str, Any]:
        payload = await self._get(f"/payments/{payment_id}")
        if not isinstance(payload, dict):
            raise DodoUnavailable("unexpected Dodo payment payload shape")
        return payload

    async def list_subscriptions(self, **filters: Any) -> list[dict[str, Any]]:
        payload = await self._get("/subscriptions", **filters)
        return _items(payload)

    async def list_refunds(self, **filters: Any) -> list[dict[str, Any]]:
        payload = await self._get("/refunds", **filters)
        return _items(payload)

    async def list_disputes(self, **filters: Any) -> list[dict[str, Any]]:
        payload = await self._get("/disputes", **filters)
        return _items(payload)

    async def list_payouts(self, **filters: Any) -> list[dict[str, Any]]:
        payload = await self._get("/payouts", **filters)
        return _items(payload)

    async def list_balance_ledger(self, **filters: Any) -> list[dict[str, Any]]:
        payload = await self._get("/balances/ledger", **filters)
        return _items(payload)


class FakeDodoClient(DodoClient):
    """In-memory client for tests and the demo degradation path."""

    def __init__(
        self,
        *,
        payments: list[dict[str, Any]] | None = None,
        subscriptions: list[dict[str, Any]] | None = None,
        refunds: list[dict[str, Any]] | None = None,
        disputes: list[dict[str, Any]] | None = None,
        payouts: list[dict[str, Any]] | None = None,
        ledger: list[dict[str, Any]] | None = None,
        unavailable: bool = False,
    ) -> None:
        super().__init__(api_key="test_readonly")
        self._payments = list(payments or [])
        self._subscriptions = list(subscriptions or [])
        self._refunds = list(refunds or [])
        self._disputes = list(disputes or [])
        self._payouts = list(payouts or [])
        self._ledger = list(ledger or [])
        self._unavailable = unavailable

    def _guard(self) -> None:
        if self._unavailable:
            raise DodoUnavailable("injected Dodo outage")

    async def list_payments(self, **filters: Any) -> list[dict[str, Any]]:
        del filters
        self._guard()
        return list(self._payments)

    async def get_payment(self, payment_id: str) -> dict[str, Any]:
        self._guard()
        for payment in self._payments:
            if payment.get("payment_id", payment.get("id")) == payment_id:
                return payment
        raise DodoUnavailable(f"payment {payment_id!r} not found")

    async def list_subscriptions(self, **filters: Any) -> list[dict[str, Any]]:
        del filters
        self._guard()
        return list(self._subscriptions)

    async def list_refunds(self, **filters: Any) -> list[dict[str, Any]]:
        del filters
        self._guard()
        return list(self._refunds)

    async def list_disputes(self, **filters: Any) -> list[dict[str, Any]]:
        del filters
        self._guard()
        return list(self._disputes)

    async def list_payouts(self, **filters: Any) -> list[dict[str, Any]]:
        del filters
        self._guard()
        return list(self._payouts)

    async def list_balance_ledger(self, **filters: Any) -> list[dict[str, Any]]:
        del filters
        self._guard()
        return list(self._ledger)


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("items", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    raise DodoUnavailable("unexpected Dodo list payload shape")


__all__ = [
    "LIVE_BASE_URL",
    "TEST_BASE_URL",
    "DodoClient",
    "DodoUnavailable",
    "FakeDodoClient",
]
