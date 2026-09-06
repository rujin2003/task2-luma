"""FastAPI entrypoint. Route modules live in `backend/api/`."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend import api
from backend.api.session import APP_VERSION
from backend.finance.policy import TreasuryPolicy

# The Next.js dev server. Narrow by origin rather than wildcard even in a demo: an API
# that answers anyone is a habit that survives into the deployment where it matters.
DEV_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


def create_app() -> FastAPI:
    app = FastAPI(title="WAR ROOM", version=APP_VERSION)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Last-Event-ID"],
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

    return app


app = create_app()
