"""The parallel agent wave, throttled honestly.

A free tier will not run nine agents at once, so the wave is *logically* parallel and
*physically* bounded: a semaphore caps what is in flight, a shared token bucket keeps the
process under the tier's requests-per-minute, and everything else waits as `queued`. That
status is real information -- "AR Agent: queued" is the truth, and a spinner claiming
otherwise is not.

What this module owns, and the runner deliberately does not:

* **The retry budget.** A timeout is retried with exponential backoff and jitter; a
  schema violation or a tool failure is not, because retrying it changes nothing.
* **The timeout verdict.** The per-agent timeout is independent of the retry budget: an
  agent gets `timeout_s` per attempt, not per wave.
* **Degradation.** An agent that never produced a finding still produces an `AgentRun`
  and a `system.degraded` event, so the Commander proceeds with reduced confidence
  instead of silently proceeding with less evidence.

`run_wave` never raises. Nine agents where one dies is a degraded wave, not a crash.
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from backend.agents.provider import LLMTimeout
from backend.agents.routing import ExecutorLimits, ModelRouting
from backend.agents.runner import AgentRunner, AgentSpec
from backend.contracts.agent import AgentFinding, AgentRun, AgentStatus, TokenUsage
from backend.contracts.events import (
    AgentQueued,
    AgentStatusChanged,
    StatusMark,
    SystemDegraded,
)
from backend.orchestrator.bus import EventBus
from backend.orchestrator.runs import RunStore

Sleeper = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class TokenBucket:
    """Process-wide request limiter, sized from the tier's documented RPM.

    Refills continuously rather than per-minute, so a wave of four does not stall for a
    whole minute behind a wave of four that came before it.
    """

    def __init__(
        self,
        requests_per_minute: int,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self.capacity = float(requests_per_minute)
        self.rate = requests_per_minute / 60.0
        self._tokens = float(requests_per_minute)
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
        self._updated = now

    async def acquire(self) -> float:
        """Take one token, waiting if the tier has none left. Returns seconds waited."""
        waited = 0.0
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return waited
                delay = (1.0 - self._tokens) / self.rate
                waited += delay
                await self._sleep(delay)


@dataclass(slots=True)
class WaveUnit:
    """One agent's place in the wave, with whatever the wave already knows."""

    spec: AgentSpec
    prior_findings: list[AgentFinding] = field(default_factory=list)
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))


class AgentExecutor:
    """Runs a wave of agents under bounded concurrency and a shared rate limit."""

    def __init__(
        self,
        *,
        runner: AgentRunner,
        routing: ModelRouting,
        bus: EventBus,
        store: RunStore | None = None,
        limits: ExecutorLimits | None = None,
        sleep: Sleeper = asyncio.sleep,
        clock: Clock = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._runner = runner
        self._routing = routing
        self._bus = bus
        self._store = store
        self._limits = limits or routing.executor
        self._sleep = sleep
        self._clock = clock
        self._jitter = jitter
        self._semaphore = asyncio.Semaphore(self._limits.max_in_flight)
        self._bucket = TokenBucket(self._limits.requests_per_minute, clock=clock, sleep=sleep)

    async def run_wave(self, units: Sequence[WaveUnit]) -> list[AgentRun]:
        """Run every unit, returning one `AgentRun` each, in the order given."""
        for position, unit in enumerate(units):
            self._bus.emit(
                AgentQueued,
                investigation_id=self._runner.investigation_id,
                status_line=f"{unit.spec.role.value}: queued",
                agent=unit.spec.role,
                run_id=unit.run_id,
                queue_position=position,
            )

        runs = await asyncio.gather(*(self._run_unit(unit) for unit in units))
        for run in runs:
            if self._store is not None:
                self._store.add(run)
            if run.status not in {AgentStatus.COMPLETE, AgentStatus.REFUSED}:
                self._bus.emit(
                    SystemDegraded,
                    investigation_id=run.investigation_id,
                    mark=StatusMark.WARN,
                    status_line=f"{run.agent.value} did not complete",
                    component=run.agent.value,
                    reason=(run.failure_reason or run.status.value)[:400],
                )
        return list(runs)

    async def _run_unit(self, unit: WaveUnit) -> AgentRun:
        route = self._routing.route(unit.spec.role)
        queued_at = self._clock()

        async with self._semaphore:
            await self._bucket.acquire()
            queued_ms = int((self._clock() - queued_at) * 1000)

            # The role decides how many attempts it is worth; the wave-wide retry budget
            # caps it, because one role must not spend the whole tier's rate limit.
            allowed = max(1, min(route.max_attempts, self._limits.max_retries))

            last_error = "timed out"
            for attempt in range(1, allowed + 1):
                started_at = datetime.now(UTC)
                try:
                    return await asyncio.wait_for(
                        self._runner.run(
                            unit.spec,
                            run_id=unit.run_id,
                            prior_findings=unit.prior_findings,
                            attempt=attempt,
                            queued_ms=queued_ms,
                        ),
                        timeout=route.timeout_s,
                    )
                except (LLMTimeout, TimeoutError) as exc:
                    # A timeout is the one failure worth paying for twice: the call may
                    # simply have been throttled behind someone else's traffic.
                    last_error = str(exc) or f"exceeded {route.timeout_s}s"
                    if attempt >= allowed:
                        return self._unfinished(
                            unit,
                            AgentStatus.TIMEOUT,
                            started_at,
                            queued_ms,
                            attempt,
                            last_error,
                        )
                    await self._backoff(attempt)
                    await self._bucket.acquire()
                except Exception as exc:
                    # A wiring bug -- an agent calling a tool outside its allowlist, say --
                    # is loud in the run record and invisible to the other eight agents.
                    return self._unfinished(
                        unit,
                        AgentStatus.FAILED,
                        started_at,
                        queued_ms,
                        attempt,
                        f"{type(exc).__name__}: {exc}",
                    )

            return self._unfinished(  # pragma: no cover -- the loop always returns
                unit, AgentStatus.TIMEOUT, datetime.now(UTC), queued_ms, allowed, last_error
            )

    async def _backoff(self, attempt: int) -> None:
        """Exponential, with jitter, so a throttled wave does not retry in lockstep."""
        delay = self._limits.backoff_base_s * (2 ** (attempt - 1))
        await self._sleep(delay + self._jitter() * self._limits.backoff_jitter_s)

    def _unfinished(
        self,
        unit: WaveUnit,
        status: AgentStatus,
        started_at: datetime,
        queued_ms: int,
        attempts: int,
        reason: str,
    ) -> AgentRun:
        """An agent that never produced a finding still produces a record and an event."""
        run = AgentRun(
            run_id=unit.run_id,
            investigation_id=self._runner.investigation_id,
            agent=unit.spec.role,
            status=status,
            model=self._routing.route(unit.spec.role).model,
            started_at=started_at,
            ended_at=datetime.now(UTC),
            queued_ms=queued_ms,
            usage=TokenUsage(),
            failure_reason=f"{reason} (after {attempts} attempts)"[:400],
            attempts=attempts,
        )
        self._bus.emit(
            AgentStatusChanged,
            investigation_id=run.investigation_id,
            mark=StatusMark.FAIL,
            status_line=f"{unit.spec.role.value}: {status.value}",
            agent=unit.spec.role,
            run_id=unit.run_id,
            status=status,
            failure_reason=run.failure_reason,
            attempt=attempts,
        )
        return run
