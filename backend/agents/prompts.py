"""System prompts live in `prompts/`, one Markdown file per role.

They are files rather than string constants for two reasons: a prompt change shows up as a
readable diff a non-engineer can review, and CI can assert every one of them fits the
system-prompt budget without importing anything.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from backend.contracts.agent import AgentRole

PROMPT_ROOT = Path(__file__).resolve().parents[2] / "prompts"


@cache
def load_prompt(role: AgentRole) -> str:
    path = PROMPT_ROOT / f"{role.value}.md"
    if not path.exists():
        raise FileNotFoundError(f"no system prompt for {role.value} at {path}")
    return path.read_text(encoding="utf-8").strip()
