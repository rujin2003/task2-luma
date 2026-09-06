"""Builders for the agent runtime tests: a stub specialist and a recording provider."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from backend.agents.replay import ReplayProvider
from backend.agents.routing import ModelRouting, load_routing
from backend.contracts import AgentFinding, AgentRole, AgentStatus
from backend.orchestrator.bus import EventBus
from backend.tools.fixtures import FixtureToolset
from backend.tools.registry import ScopedToolset

# A resolvable citation the AR fixtures actually return.
INVOICE_REF = "ar_ledger:INV-10482#amount_due"


@pytest.fixture(scope="session")
def routing() -> ModelRouting:
    return load_routing()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def toolset() -> FixtureToolset:
    return FixtureToolset()


@dataclass
class StubSpec:
    """A specialist reduced to what the runner needs: a prompt, a task, a gather."""

    role: AgentRole = AgentRole.AR_COLLECTIONS
    system_prompt: str = "You are the AR Collections agent. Cite every number."
    task: str = "Rank this week's realistic collection acceleration."
    tools: list[str] = field(default_factory=lambda: ["rank_collection_opportunities"])
    lines: list[str] | None = None

    def review(self, finding: AgentFinding) -> AgentFinding:
        return finding

    async def gather(self, tools: ScopedToolset) -> list[str]:
        lines: list[str] = []
        for tool in self.tools:
            payload = await tools.call(tool)
            lines.append(f"{tool}: {len(payload.references)} rows")
        return self.lines if self.lines is not None else lines


def finding_payload(
    *,
    agent: AgentRole = AgentRole.AR_COLLECTIONS,
    status: AgentStatus = AgentStatus.COMPLETE,
    references: list[str] | None = None,
    headline: str = "$2.6M of AR is realistically accelerable inside three weeks",
    **extra: Any,
) -> dict[str, Any]:
    """A model output shaped like a finding, with the excerpt deliberately wrong.

    The validator is expected to overwrite it from the ledger -- a test that pre-agrees
    with the source row would not notice if it stopped doing so.
    """
    return {
        "agent": agent.value,
        "status": status.value,
        "headline": headline,
        "evidence": [
            {
                "reference": reference,
                "source": reference.split(":", 1)[0],
                "excerpt": "a paraphrase the model would like to show the treasurer",
            }
            for reference in (references if references is not None else [INVOICE_REF])
        ],
        **extra,
    }


def recording_provider(
    root: Path, recordings: dict[AgentRole, dict[str, Any]], *, strict: bool = False
) -> ReplayProvider:
    """Write one default recording per role and return a provider that replays them."""
    root.mkdir(parents=True, exist_ok=True)
    for role, recording in recordings.items():
        payload = [{"agent": role.value, "default": True, **recording}]
        (root / f"{role.value}.json").write_text(json.dumps(payload), encoding="utf-8")
    return ReplayProvider(root, strict=strict)
