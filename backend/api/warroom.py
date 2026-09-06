"""The escalation branch: open the war room, watch it live, read what it concluded.

The stream is the interesting endpoint. It is plain SSE rather than a websocket because
the traffic is strictly one-way -- the browser watches an investigation, it does not steer
one -- and because `Last-Event-ID` gives resume-on-reconnect for free. The bus assigns
`seq`, so a client that drops at 41 and reconnects gets 42 onwards and misses nothing.

What does *not* go down this stream is any model's reasoning. There is no field for it in
the event schema and there is no endpoint for it here. Findings, evidence, decisions and
status; that is the whole vocabulary.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.api.session import Session, get_session
from backend.contracts.agent import AgentRun
from backend.contracts.events import EVENT_SCHEMA_VERSION
from backend.contracts.strategy import Recommendation
from backend.orchestrator.investigation import InvestigationResult

router = APIRouter(prefix="/api/war-room", tags=["war-room"])

SessionDep = Annotated[Session, Depends(get_session)]

# Proxies buffer `text/event-stream` by default and an investigation that arrives in one
# lump at the end is not a live feed. This is the nginx opt-out.
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class WarRoomStatus(BaseModel):
    """Whether there is a war room at all. The screen only exists during an escalation."""

    schema_version: str = EVENT_SCHEMA_VERSION
    open: bool
    escalate: bool
    result: InvestigationResult | None = None
    runs: list[AgentRun] = Field(default_factory=list)
    next_seq: int


@router.get("")
async def status(session: SessionDep) -> WarRoomStatus:
    investigation = session.investigation
    return WarRoomStatus(
        open=investigation is not None,
        escalate=bool(session.policy and session.policy.escalate),
        result=investigation,
        runs=(
            session.runs.all(investigation_id=investigation.investigation_id)
            if investigation
            else []
        ),
        next_seq=session.bus.seq,
    )


@router.post("/open")
async def open_war_room(session: SessionDep) -> InvestigationResult:
    """Opens only on a breach the policy check found. Never on a request alone."""
    return await session.open_war_room()


@router.get("/recommendation")
async def recommendation(session: SessionDep) -> Recommendation:
    """The worklist, the rejections, the stress results and the attempts that failed."""
    return session.require_recommendation()


@router.get("/events")
async def events(
    session: SessionDep,
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID")] = None,
    replay_from: Annotated[int | None, Query(ge=0)] = None,
) -> StreamingResponse:
    """Live SSE. Replays history first, so a late-joining tab sees the run from the start.

    `Last-Event-ID` wins over `replay_from`: the browser's own resume cursor is a fact
    about what it has already rendered, and a query parameter is only ever a preference.
    """
    if last_event_id is not None:
        cursor = last_event_id + 1
    elif replay_from is not None:
        cursor = replay_from
    else:
        cursor = 0

    return StreamingResponse(
        session.bus.stream(replay_from=max(cursor, 0)),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/history")
async def history(session: SessionDep, since: Annotated[int, Query(ge=0)] = 0) -> dict[str, object]:
    """The same events as a plain array, for a client that cannot hold a stream open."""
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "next_seq": session.bus.seq,
        "events": session.bus.history(since=since),
    }
