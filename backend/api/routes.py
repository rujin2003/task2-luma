"""FastAPI surface for the weekly cycle, war room stream, and recommendations."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.agents.fake import FakeProvider
from backend.agents.routing import load_routing
from backend.contracts.approvals import ApprovalDecision, ApprovalRole, check_maker_checker
from backend.contracts.events import EVENT_SCHEMA_VERSION
from backend.orchestrator.session import SESSION
from backend.orchestrator.weekly_cycle import run_monday_cycle
from backend.tools.fixtures import FixtureToolset
from backend.tools.toolset import Toolset

router = APIRouter()


class CycleRunRequest(BaseModel):
    company: str = "NovaTech Industries"
    as_of: str = "2026-03-02"
    auto_investigate: bool = True
    incident_kind: str | None = None
    use_fixtures: bool = True


class CycleRunResponse(BaseModel):
    forecast_version_id: str
    published: bool
    breached: bool
    investigation_id: str | None = None
    recommendation_id: str | None = None
    steps_completed: list[int]
    event_count: int


def _tools(*, use_fixtures: bool) -> Toolset:
    del use_fixtures  # Engine session wiring lands with a DB-backed Toolset; fixtures for demo.
    return FixtureToolset()


@router.get("/health")
def api_health() -> dict[str, str]:
    return {"status": "ok", "event_schema": EVENT_SCHEMA_VERSION}


@router.post("/cycle/run", response_model=CycleRunResponse)
async def cycle_run(body: CycleRunRequest) -> CycleRunResponse:
    """Run the Monday cycle; open the war room when policy is breached."""
    SESSION.reset()
    bus = SESSION.bus
    result = await run_monday_cycle(
        tools=_tools(use_fixtures=body.use_fixtures),
        bus=bus,
        provider=FakeProvider(strict=True),
        routing=load_routing(),
        company=body.company,
        as_of=body.as_of,
        auto_investigate=body.auto_investigate,
        incident_kind=body.incident_kind,
    )
    SESSION.remember_cycle(result)
    investigation_id = None
    recommendation_id = None
    if result.investigation is not None:
        investigation_id = result.investigation.investigation_id
        if result.investigation.recommendation is not None:
            recommendation_id = result.investigation.recommendation.recommendation_id
    return CycleRunResponse(
        forecast_version_id=result.forecast_version_id,
        published=result.published,
        breached=result.breach is not None,
        investigation_id=investigation_id,
        recommendation_id=recommendation_id,
        steps_completed=list(result.steps_completed),
        event_count=bus.seq,
    )


@router.get("/events")
async def event_stream(
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """SSE feed of War Room activity."""
    replay_from = 0
    if last_event_id is not None:
        try:
            replay_from = int(last_event_id) + 1
        except ValueError:
            replay_from = 0

    async def frames():
        async for frame in SESSION.bus.stream(replay_from=replay_from):
            yield frame

    return StreamingResponse(frames(), media_type="text/event-stream")


@router.get("/events/history")
def event_history(since: int = 0) -> list[dict[str, object]]:
    return [e.model_dump(mode="json") for e in SESSION.bus.history(since=since)]


@router.get("/recommendation")
def get_recommendation() -> dict[str, object]:
    if SESSION.recommendation is None:
        raise HTTPException(status_code=404, detail="no recommendation yet; run /cycle/run")
    return SESSION.recommendation.model_dump(mode="json")


@router.get("/investigation")
def get_investigation() -> dict[str, object]:
    if SESSION.investigation is None:
        raise HTTPException(status_code=404, detail="no open investigation")
    inv = SESSION.investigation
    return {
        "investigation_id": inv.investigation_id,
        "phase": inv.phase.value,
        "plan_id": inv.plan_id,
        "breach": inv.breach.model_dump(mode="json"),
        "findings": [f.model_dump(mode="json") for f in inv.findings],
        "strategies": [s.model_dump(mode="json") for s in inv.strategies],
        "stress_results": [r.model_dump(mode="json") for r in inv.stress_results],
        "replan_history": [r.model_dump(mode="json") for r in inv.replan_history],
        "recommendation_id": (inv.recommendation.recommendation_id if inv.recommendation else None),
    }


@router.get("/approvals")
def list_approvals() -> list[dict[str, object]]:
    return [r.model_dump(mode="json") for r in SESSION.approvals.values()]


class DecideBody(BaseModel):
    request_id: str
    decided_by: str = Field(min_length=1)
    approved: bool
    rationale: str = Field(min_length=1, max_length=600)
    role: str = "cfo"


@router.post("/approvals/decide")
def decide_approval(body: DecideBody) -> dict[str, object]:
    request = SESSION.approvals.get(body.request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="unknown approval request")

    try:
        role = ApprovalRole(body.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid role: {body.role}") from exc

    decision = ApprovalDecision(
        request_id=body.request_id,
        decided_by=body.decided_by,
        decided_by_role=role,
        approved=body.approved,
        reason=body.rationale,
        decided_at=datetime.now(UTC),
        data_snapshot_ref=request.data_snapshot_ref,
    )
    try:
        check_maker_checker(request, decision)
    except Exception as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    updated = SESSION.decide_approval(decision)
    return {
        "request": updated.model_dump(mode="json"),
        "decision": decision.model_dump(mode="json"),
    }


@router.get("/forecast/snapshot")
async def forecast_snapshot() -> dict[str, object]:
    """Shape the Forecast screen can consume from fixtures/engine tools."""
    tools = FixtureToolset()
    liquidity = await tools.get_liquidity_position()
    forecast = await tools.get_forecast_summary()
    variance = await tools.get_variance_bridge(top_n=25)
    assumptions = await tools.list_driver_assumptions(top_n=25)
    errors = await tools.get_forecast_error_percentiles(horizon_weeks=6)
    return {
        "as_of": liquidity.as_of.isoformat() if liquidity.as_of else None,
        "currency": liquidity.cash_today.currency,
        "forecast_version_id": forecast.version_id,
        "published": forecast.published,
        "liquidity": liquidity.model_dump(mode="json"),
        "weeks": [w.model_dump(mode="json") for w in forecast.weeks],
        "variance_bridge": [r.model_dump(mode="json") for r in variance.rows],
        "exceptions": [
            {
                "id": row.reference,
                "category": row.category,
                "delta": row.delta.model_dump(mode="json"),
                "material": row.material,
                "reference": row.reference,
            }
            for row in variance.rows
            if row.material
        ],
        "accuracy": [p.model_dump(mode="json") for p in errors.percentiles],
        "assumptions": [a.model_dump(mode="json") for a in assumptions.rows],
    }
