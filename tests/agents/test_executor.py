"""The parallel wave: bounded, rate-limited, and honest about what did not finish.

The wave is the shape of the demo. What these tests protect is the promise that a
throttled or failing agent degrades the recommendation's confidence rather than the run:
every agent produces an `AgentRun`, including the ones that never produced a finding.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.agents.routing import ExecutorLimits
from backend.agents.runner import AgentRunner
from backend.contracts import AgentRole, AgentStatus
from backend.contracts.events import EventType
from backend.orchestrator.bus import EventBus
from backend.orchestrator.executor import AgentExecutor, TokenBucket, WaveUnit
from backend.orchestrator.runs import InMemoryRunStore
from backend.tools.registry import ScopedToolset
from tests.agents.conftest import StubSpec, finding_payload, recording_provider


class FakeTime:
    """Deterministic clock and sleep, so a retry test costs no wall clock."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


class CountingSpec(StubSpec):
    """Records how many of these were inside `gather` at once."""

    def __init__(self, role: AgentRole, tools: list[str], tracker: dict[str, int]) -> None:
        super().__init__(role=role, tools=tools)
        self._tracker = tracker

    async def gather(self, tools: ScopedToolset) -> list[str]:
        self._tracker["live"] += 1
        self._tracker["peak"] = max(self._tracker["peak"], self._tracker["live"])
        await asyncio.sleep(0)
        try:
            return await super().gather(tools)
        finally:
            self._tracker["live"] -= 1


def build(
    tmp_path: Path,
    bus: EventBus,
    toolset,
    routing,
    recordings,
    *,
    limits: ExecutorLimits | None = None,
    time_source: FakeTime | None = None,
    store: InMemoryRunStore | None = None,
) -> AgentExecutor:
    provider = recording_provider(tmp_path, recordings)
    runner = AgentRunner(
        provider=provider,
        toolset=toolset,
        routing=routing,
        bus=bus,
        company="NovaTech Industries",
        as_of="2026-03-02",
        investigation_id="inv-1",
    )
    clock = time_source or FakeTime()
    return AgentExecutor(
        runner=runner,
        routing=routing,
        bus=bus,
        store=store,
        limits=limits or ExecutorLimits(max_in_flight=4, requests_per_minute=60, max_retries=2),
        sleep=clock.sleep,
        clock=clock.clock,
        jitter=lambda: 0.5,
    )


AR = StubSpec()
VARIANCE = StubSpec(
    role=AgentRole.VARIANCE,
    system_prompt="You are the Variance agent. Root-cause each material delta.",
    task="Explain the material deltas in the W09 bridge.",
    tools=["get_variance_bridge"],
)


async def test_a_wave_survives_an_agent_that_never_answers(
    tmp_path: Path, bus: EventBus, toolset, routing
) -> None:
    """The phase's own acceptance test: two agents, one times out, two run records."""
    store = InMemoryRunStore()
    executor = build(
        tmp_path,
        bus,
        toolset,
        routing,
        {
            AgentRole.AR_COLLECTIONS: {"output": finding_payload()},
            AgentRole.VARIANCE: {"raises": "timeout"},
        },
        store=store,
    )

    runs = await executor.run_wave([WaveUnit(AR), WaveUnit(VARIANCE)])
    by_agent = {run.agent: run for run in runs}

    assert by_agent[AgentRole.AR_COLLECTIONS].status is AgentStatus.COMPLETE
    assert by_agent[AgentRole.VARIANCE].status is AgentStatus.TIMEOUT
    assert by_agent[AgentRole.VARIANCE].failure_reason
    assert len(store.all(investigation_id="inv-1")) == 2

    types = [event.type for event in bus.history()]
    assert types.count(EventType.AGENT_QUEUED) == 2
    assert EventType.SYSTEM_DEGRADED in types, "a missing agent is visible, not silent"
    degraded = [e for e in bus.history() if e.type is EventType.SYSTEM_DEGRADED]
    assert degraded[0].component == "variance"


async def test_a_timeout_is_retried_with_backoff_and_jitter(
    tmp_path: Path, bus: EventBus, toolset, routing
) -> None:
    clock = FakeTime()
    executor = build(
        tmp_path,
        bus,
        toolset,
        routing,
        {AgentRole.VARIANCE: {"raises": "timeout"}},
        limits=ExecutorLimits(
            max_in_flight=2,
            requests_per_minute=60,
            max_retries=3,
            backoff_base_s=1.0,
            backoff_jitter_s=0.5,
        ),
        time_source=clock,
    )

    (run,) = await executor.run_wave([WaveUnit(VARIANCE)])

    # `max_attempts` on the role is 2, and the wave-wide budget allows 3: the role wins.
    assert run.attempts == 2
    assert run.status is AgentStatus.TIMEOUT
    assert clock.slept == [1.25], "one backoff, exponential base plus half the jitter"


async def test_concurrency_is_bounded_even_when_the_wave_is_wide(
    tmp_path: Path, bus: EventBus, toolset, routing
) -> None:
    tracker = {"live": 0, "peak": 0}
    executor = build(
        tmp_path,
        bus,
        toolset,
        routing,
        {
            AgentRole.AR_COLLECTIONS: {"output": finding_payload()},
            AgentRole.VARIANCE: {"output": finding_payload(agent=AgentRole.VARIANCE)},
        },
        limits=ExecutorLimits(max_in_flight=1, requests_per_minute=60, max_retries=1),
    )
    units = [
        WaveUnit(CountingSpec(AgentRole.AR_COLLECTIONS, ["rank_collection_opportunities"], tracker))
        for _ in range(3)
    ] + [WaveUnit(CountingSpec(AgentRole.VARIANCE, ["get_variance_bridge"], tracker))]

    runs = await executor.run_wave(units)

    assert tracker["peak"] == 1
    assert all(run.status is AgentStatus.COMPLETE for run in runs)


async def test_queue_time_is_measured_not_guessed(
    tmp_path: Path, bus: EventBus, toolset, routing
) -> None:
    """'AR Agent: queued' is honest UI only if the wait is real and recorded."""
    clock = FakeTime()
    executor = build(
        tmp_path,
        bus,
        toolset,
        routing,
        {AgentRole.AR_COLLECTIONS: {"output": finding_payload()}},
        limits=ExecutorLimits(max_in_flight=4, requests_per_minute=1, max_retries=1),
        time_source=clock,
    )

    runs = await executor.run_wave([WaveUnit(AR), WaveUnit(StubSpec())])
    waited = sorted(run.queued_ms or 0 for run in runs)

    assert waited[0] == 0, "the first agent through the bucket does not wait"
    assert waited[1] == 60_000, "at one request per minute, the second waits a minute"


async def test_a_wiring_bug_fails_one_agent_not_the_wave(
    tmp_path: Path, bus: EventBus, toolset, routing
) -> None:
    executor = build(
        tmp_path,
        bus,
        toolset,
        routing,
        {
            AgentRole.AR_COLLECTIONS: {"output": finding_payload()},
            AgentRole.DODO_REVENUE: {"output": finding_payload(agent=AgentRole.DODO_REVENUE)},
        },
    )
    trespassing = StubSpec(role=AgentRole.DODO_REVENUE, tools=["rank_collection_opportunities"])

    runs = await executor.run_wave([WaveUnit(AR), WaveUnit(trespassing)])
    by_agent = {run.agent: run for run in runs}

    assert by_agent[AgentRole.AR_COLLECTIONS].status is AgentStatus.COMPLETE
    assert by_agent[AgentRole.DODO_REVENUE].status is AgentStatus.FAILED
    assert "ToolNotAllowed" in (by_agent[AgentRole.DODO_REVENUE].failure_reason or "")


# --- the limiter itself ---------------------------------------------------------------


async def test_the_bucket_refills_continuously() -> None:
    clock = FakeTime()
    bucket = TokenBucket(60, clock=clock.clock, sleep=clock.sleep)

    for _ in range(60):
        assert await bucket.acquire() == 0.0

    assert await bucket.acquire() == pytest.approx(1.0), "60/min is one per second"
