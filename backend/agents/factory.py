"""Construct the process-wide LLM provider from environment.

Demo and CI stay on `FakeProvider`. Live Gemini is opt-in via `WARROOM_LLM=gemini`
(or `auto` when `GEMINI_API_KEY` is present).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from backend.agents.fake import FakeProvider
from backend.agents.gemini import GeminiProvider
from backend.agents.provider import LLMProvider

_ROOT = Path(__file__).resolve().parents[2]
_ENV_LOADED = False


def load_env(*, override: bool = False) -> None:
    """Load `.env` from the repo root once. Safe to call repeatedly.

    Values already present in the process environment win unless `override=True`,
    so CI can force `WARROOM_LLM=fake` without the local `.env` flipping it back.
    """
    global _ENV_LOADED
    if _ENV_LOADED and not override:
        return
    load_dotenv(_ROOT / ".env", override=override)
    _ENV_LOADED = True


def build_provider(*, mode: str | None = None) -> LLMProvider:
    """Return Fake or Gemini based on `WARROOM_LLM` / `GEMINI_API_KEY`."""
    load_env()
    chosen = (mode or os.environ.get("WARROOM_LLM", "auto")).strip().lower()
    if chosen in {"fake", "fixture", "fixtures"}:
        return FakeProvider()
    if chosen == "gemini":
        return GeminiProvider.from_env()
    if chosen == "auto":
        if os.environ.get("GEMINI_API_KEY", "").strip():
            return GeminiProvider.from_env()
        return FakeProvider()
    raise ValueError(f"unknown WARROOM_LLM={chosen!r}; expected fake|gemini|auto")
