"""Construct the process-wide LLM provider from environment.

`WARROOM_LLM` selects the backend:

* `replay` -- replay recorded completions from `tests/fixtures/llm/`. Deterministic,
  offline and free; this is what `make demo` and CI run on, so the golden path is
  byte-identical on every machine.
* `gemini` -- call the live model. Requires `GEMINI_API_KEY`.
* `auto` (default) -- Gemini when a key is present, replay otherwise.

`fake`/`fixture`/`fixtures` remain accepted spellings of `replay` so existing scripts
and shell history keep working.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from backend.agents.gemini import GeminiProvider
from backend.agents.provider import LLMProvider
from backend.agents.replay import ReplayProvider

_ROOT = Path(__file__).resolve().parents[2]
_ENV_LOADED = False

_REPLAY_ALIASES = {"replay", "fake", "fixture", "fixtures"}


def load_env(*, override: bool = False) -> None:
    """Load `.env` from the repo root once. Safe to call repeatedly.

    Values already present in the process environment win unless `override=True`,
    so CI can force `WARROOM_LLM=replay` without the local `.env` flipping it back.
    """
    global _ENV_LOADED
    if _ENV_LOADED and not override:
        return
    load_dotenv(_ROOT / ".env", override=override)
    _ENV_LOADED = True


def build_provider(*, mode: str | None = None) -> LLMProvider:
    """Return the replay or Gemini provider per `WARROOM_LLM` / `GEMINI_API_KEY`."""
    load_env()
    chosen = (mode or os.environ.get("WARROOM_LLM", "auto")).strip().lower()
    if chosen in _REPLAY_ALIASES:
        return ReplayProvider()
    if chosen == "gemini":
        return GeminiProvider.from_env()
    if chosen == "auto":
        if os.environ.get("GEMINI_API_KEY", "").strip():
            return GeminiProvider.from_env()
        return ReplayProvider()
    raise ValueError(f"unknown WARROOM_LLM={chosen!r}; expected replay|gemini|auto")
