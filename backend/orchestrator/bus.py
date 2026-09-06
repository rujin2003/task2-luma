"""The event bus behind the live War Room stream.

One process-wide sequence, one append-only history, many subscribers. The `seq` is
assigned here and nowhere else, which is what makes it a usable resume cursor: a client
that reconnects with `Last-Event-ID: 41` gets 42 onwards and misses nothing.

Two deliberate choices:

* **History is bounded and replayable.** The War Room is only open for minutes, so the
  whole investigation fits in a ring buffer. A late-joining browser sees the run from the
  start rather than joining mid-sentence.
* **A slow subscriber is dropped, never backpressured.** A stalled browser tab must not
  be able to stall the investigation producing the events.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

from backend.contracts.events import (
    EVENT_SCHEMA_VERSION,
    StreamHeartbeat,
    StreamOpened,
    WarRoomEvent,
    _Event,
)

HISTORY_LIMIT = 2000
SUBSCRIBER_QUEUE_LIMIT = 256
HEARTBEAT_S = 15.0


class Subscriber:
    """One connected client. Bounded queue; overflow marks the client stale."""

    def __init__(self, limit: int = SUBSCRIBER_QUEUE_LIMIT) -> None:
        self.queue: asyncio.Queue[WarRoomEvent] = asyncio.Queue(maxsize=limit)
        self.dropped = 0

    def offer(self, event: WarRoomEvent) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            # The client is not keeping up. Losing its stream is strictly better than
            # letting it throttle the investigation.
            self.dropped += 1


class EventBus:
    """Assigns `seq`, keeps history, fans out to subscribers."""

    def __init__(
        self,
        *,
        history_limit: int = HISTORY_LIMIT,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._history: deque[WarRoomEvent] = deque(maxlen=history_limit)
        self._subscribers: set[Subscriber] = set()
        self._seq = 0

    # --- producing -------------------------------------------------------------------

    def emit[EventT: _Event](self, event_type: type[EventT], /, **fields: Any) -> EventT:
        """Stamp an event with the next `seq` and the current time, then publish it."""
        event = event_type(seq=self._seq, ts=self._clock(), **fields)
        self._seq += 1
        self._publish(event)
        return event

    def _publish(self, event: Any) -> None:
        self._history.append(event)
        for subscriber in tuple(self._subscribers):
            subscriber.offer(event)

    # --- consuming -------------------------------------------------------------------

    @property
    def seq(self) -> int:
        """The seq the next emitted event will carry."""
        return self._seq

    def history(self, *, since: int = 0) -> list[WarRoomEvent]:
        return [event for event in self._history if event.seq >= since]

    def subscribe(self, *, limit: int = SUBSCRIBER_QUEUE_LIMIT) -> Subscriber:
        subscriber = Subscriber(limit)
        self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.discard(subscriber)

    async def stream(
        self, *, replay_from: int = 0, heartbeat_s: float = HEARTBEAT_S
    ) -> AsyncIterator[str]:
        """Yield `text/event-stream` frames: an opener, the replay, then live events.

        The heartbeat carries the current cursor rather than advancing it, so an idle
        stream stays open through a proxy without inventing history.
        """
        subscriber = self.subscribe()
        try:
            yield StreamOpened(
                seq=max(replay_from, 0),
                ts=self._clock(),
                status_line=f"War Room stream open (schema {EVENT_SCHEMA_VERSION})",
                replay_from=replay_from,
            ).to_sse()

            replayed = -1
            for event in self.history(since=replay_from):
                replayed = event.seq
                yield event.to_sse()

            while True:
                try:
                    event = await asyncio.wait_for(subscriber.queue.get(), timeout=heartbeat_s)
                except TimeoutError:
                    yield StreamHeartbeat(
                        seq=max(self._seq - 1, 0),
                        ts=self._clock(),
                        status_line="heartbeat",
                    ).to_sse()
                    continue
                if event.seq <= replayed:
                    continue  # already delivered by the replay above
                yield event.to_sse()
        finally:
            self.unsubscribe(subscriber)
