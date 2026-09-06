"""Where `AgentRun` records live.

Every run is kept: the completions, the refusals, the timeouts and the runs whose evidence
was rejected. That is the point -- "the AR agent timed out and we proceeded on five of six
findings" is exactly the kind of thing a treasurer is entitled to see afterwards, and it
is the kind of thing that quietly disappears when only successes are stored.

`RunStore` is the seam. The in-memory store is what the tests and the demo use; the JSONL
store gives a durable audit trail without coupling the orchestrator to the ORM.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Protocol

from backend.contracts.agent import AgentRole, AgentRun, AgentStatus


class RunStore(Protocol):
    def add(self, run: AgentRun) -> None: ...

    def all(self, *, investigation_id: str | None = None) -> list[AgentRun]: ...


class InMemoryRunStore:
    """Ordered by insertion, which is the order the wave finished in."""

    def __init__(self) -> None:
        self._runs: list[AgentRun] = []

    def add(self, run: AgentRun) -> None:
        self._runs.append(run)

    def all(self, *, investigation_id: str | None = None) -> list[AgentRun]:
        if investigation_id is None:
            return list(self._runs)
        return [run for run in self._runs if run.investigation_id == investigation_id]

    def get(self, run_id: str) -> AgentRun | None:
        return next((run for run in self._runs if run.run_id == run_id), None)

    def by_agent(self, agent: AgentRole) -> list[AgentRun]:
        return [run for run in self._runs if run.agent is agent]

    def status_counts(self, *, investigation_id: str | None = None) -> dict[AgentStatus, int]:
        return dict(Counter(run.status for run in self.all(investigation_id=investigation_id)))

    def tokens_used(self, *, investigation_id: str | None = None) -> int:
        return sum(run.usage.total for run in self.all(investigation_id=investigation_id))


class JsonlRunStore(InMemoryRunStore):
    """Appends each run to a JSONL file as well as holding it in memory."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, run: AgentRun) -> None:
        super().add(run)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(run.model_dump_json() + "\n")

    def load(self) -> list[AgentRun]:
        if not self.path.exists():
            return []
        return [
            AgentRun.model_validate(json.loads(line))
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
