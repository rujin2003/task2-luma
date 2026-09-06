"""The event bus: one sequence, a replayable history, and no client that can stall it."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.contracts import AgentRole
from backend.contracts.events import (
    EVENT_SCHEMA_VERSION,
    AgentStarted,
    EventType,
    InvestigationOpened,
    parse_event,
)
from backend.orchestrator.bus import EventBus

TS = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def open_investigation(bus: EventBus) -> InvestigationOpened:
    return bus.emit(
        InvestigationOpened,
        investigation_id="inv-1",
        status_line="Minimum cash breaches the floor at W6",
        trigger="min cash $18.4M against a $20.0M floor",
        detected_at=TS,
    )


def start_agent(bus: EventBus, agent: AgentRole = AgentRole.VARIANCE) -> AgentStarted:
    return bus.emit(
        AgentStarted,
        investigation_id="inv-1",
        status_line=f"{agent.value}: working",
        agent=agent,
        run_id="run-1",
    )


def test_the_sequence_is_assigned_here_and_only_here() -> None:
    bus = EventBus(clock=lambda: TS)

    first = open_investigation(bus)
    second = start_agent(bus)

    assert (first.seq, second.seq) == (0, 1)
    assert bus.seq == 2, "the cursor points at what comes next"
    assert first.ts == TS


def test_history_replays_from_a_cursor() -> None:
    bus = EventBus(clock=lambda: TS)
    open_investigation(bus)
    start_agent(bus)
    start_agent(bus, AgentRole.AR_COLLECTIONS)

    assert [event.seq for event in bus.history(since=1)] == [1, 2]


def test_history_is_bounded_so_a_long_run_cannot_exhaust_memory() -> None:
    bus = EventBus(history_limit=2, clock=lambda: TS)
    for _ in range(5):
        start_agent(bus)

    assert [event.seq for event in bus.history()] == [3, 4]


async def test_a_stalled_client_loses_its_stream_not_the_investigation() -> None:
    bus = EventBus(clock=lambda: TS)
    slow = bus.subscribe(limit=2)

    for _ in range(5):
        start_agent(bus)

    assert slow.queue.qsize() == 2
    assert slow.dropped == 3
    assert bus.seq == 5, "the producer never blocked"


async def test_a_late_subscriber_sees_the_run_from_the_beginning() -> None:
    bus = EventBus(clock=lambda: TS)
    open_investigation(bus)
    start_agent(bus)

    frames: list[str] = []
    stream = bus.stream(replay_from=0, heartbeat_s=0.01)
    frames.append(await anext(stream))  # the opener
    frames.append(await anext(stream))
    frames.append(await anext(stream))

    assert frames[0].startswith("id: 0\nevent: stream.opened")
    assert EVENT_SCHEMA_VERSION in frames[0]
    assert "event: investigation.opened" in frames[1]
    assert "event: agent.started" in frames[2]
    await stream.aclose()


async def test_live_events_follow_the_replay_without_duplicating_it() -> None:
    bus = EventBus(clock=lambda: TS)
    open_investigation(bus)

    stream = bus.stream(replay_from=0, heartbeat_s=5.0)
    await anext(stream)  # opener
    replayed = await anext(stream)
    start_agent(bus)
    live = await anext(stream)

    assert parse_event(replayed.split("data: ", 1)[1]).seq == 0
    assert parse_event(live.split("data: ", 1)[1]).seq == 1
    await stream.aclose()


async def test_an_idle_stream_heartbeats_without_inventing_history() -> None:
    bus = EventBus(clock=lambda: TS)
    open_investigation(bus)

    stream = bus.stream(replay_from=1, heartbeat_s=0.01)
    await anext(stream)  # opener
    beat = await asyncio.wait_for(anext(stream), timeout=1.0)

    assert "event: stream.heartbeat" in beat
    assert bus.seq == 1, "a heartbeat is not an event in the investigation"
    await stream.aclose()


def test_every_frame_is_parseable_by_the_frontends_decoder() -> None:
    bus = EventBus(clock=lambda: TS)
    event = open_investigation(bus)

    frame = event.to_sse()
    _, type_line, data_line, _, _ = frame.split("\n")

    assert type_line == f"event: {EventType.INVESTIGATION_OPENED.value}"
    assert parse_event(data_line.removeprefix("data: ")) == event


def test_an_unknown_field_is_a_bug_the_bus_refuses_to_publish() -> None:
    bus = EventBus(clock=lambda: TS)

    with pytest.raises(ValidationError):
        bus.emit(AgentStarted, status_line="typo", agent=AgentRole.VARIANCE)  # no run_id
