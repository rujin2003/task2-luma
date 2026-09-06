"""FastAPI entrypoint. Route modules live in `backend/api/`."""

from __future__ import annotations

import os

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from backend import api
from backend.api.session import APP_VERSION
from backend.finance.policy import TreasuryPolicy
from backend.integrations.dodo.webhooks import (
    WebhookStore,
    WebhookVerificationError,
    receive_webhook,
)

# The Next.js dev server. Narrow by origin rather than wildcard even in a demo: an API
# that answers anyone is a habit that survives into the deployment where it matters.
DEV_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]

_webhook_store = WebhookStore()


def create_app() -> FastAPI:
    app = FastAPI(title="WAR ROOM", version=APP_VERSION)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Last-Event-ID", "webhook-id", "webhook-timestamp", "webhook-signature"],
    )
    api.errors.install(app)
    app.include_router(api.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": APP_VERSION}

    @app.get("/policy/current")
    def current_policy() -> dict[str, object]:
        policy = TreasuryPolicy.load()
        return policy.model_dump(mode="json")

    @app.post("/webhooks/dodo")
    async def dodo_webhook(
        request: Request,
        webhook_id: str | None = Header(default=None, alias="webhook-id"),
        webhook_timestamp: str | None = Header(default=None, alias="webhook-timestamp"),
        webhook_signature: str | None = Header(default=None, alias="webhook-signature"),
    ) -> dict[str, object]:
        secret = os.environ.get("DODO_PAYMENTS_WEBHOOK_KEY", "")
        if not secret:
            raise HTTPException(status_code=503, detail="webhook secret not configured")
        body = await request.body()
        try:
            event = receive_webhook(
                secret=secret,
                headers={
                    "webhook-id": webhook_id or "",
                    "webhook-timestamp": webhook_timestamp or "",
                    "webhook-signature": webhook_signature or "",
                },
                body=body,
                store=_webhook_store,
            )
        except WebhookVerificationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return {
            "received": True,
            "duplicate": event.duplicate,
            "event_type": event.event_type,
            "webhook_id": event.webhook_id,
        }

    return app


app = create_app()
