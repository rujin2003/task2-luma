"""Read-only Dodo client placeholder for deployments without credentials."""

from __future__ import annotations

from typing import Any


class DodoUnavailable(RuntimeError):
    """Dodo data cannot currently be read."""


class DodoClient:
    """Minimal read-only boundary; write operations are intentionally absent."""

    def __init__(self, *, api_key: str | None = None) -> None:
        self.api_key = api_key

    async def list_payments(self, **filters: Any) -> list[dict[str, Any]]:
        del filters
        raise DodoUnavailable("Dodo payment reads are not configured")

    async def get_payment(self, payment_id: str) -> dict[str, Any]:
        del payment_id
        raise DodoUnavailable("Dodo payment reads are not configured")


__all__ = ["DodoClient", "DodoUnavailable"]
