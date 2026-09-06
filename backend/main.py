"""Minimal FastAPI entrypoint. Route modules are Person 2's surface."""

from __future__ import annotations

from fastapi import FastAPI

from backend.finance.policy import TreasuryPolicy

app = FastAPI(title="WAR ROOM", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/policy/current")
def current_policy() -> dict[str, object]:
    policy = TreasuryPolicy.load()
    return policy.model_dump(mode="json")
