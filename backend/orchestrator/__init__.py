"""Investigation orchestration: the event bus, the executor, and the Commander path.

Eager imports here must stay free of `executor` — `AgentRunner` imports `EventBus` through
this package, and `AgentExecutor` imports `AgentRunner`. Pulling the executor in at package
import time closes that loop.
"""

from backend.orchestrator.bus import EventBus
from backend.orchestrator.runs import InMemoryRunStore, JsonlRunStore, RunStore

__all__ = [
    "AgentExecutor",
    "EventBus",
    "InMemoryRunStore",
    "JsonlRunStore",
    "RunStore",
    "WaveUnit",
]


def __getattr__(name: str):
    if name in {"AgentExecutor", "WaveUnit"}:
        from backend.orchestrator.executor import AgentExecutor, WaveUnit

        return AgentExecutor if name == "AgentExecutor" else WaveUnit
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
