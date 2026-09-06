"""What the screens are looking at, and the numbers every screen shares.

Two jobs, and they belong together:

* **The data source.** Which tenant is loaded, when it was loaded, what rows came with it
  and which sources it does *not* have. Every screen asks this first, and renders its
  empty state when the answer is `null`. Choosing a source is the only way to fill the
  product with numbers, and loading a new one empties it again.
* **The position.** Liquidity, the thirteen-week series, the aging and the drivers, read
  through the tool layer rather than computed here. That matters more than it looks: the
  Forecast screen and the Cash Forecast agent then see the same figure from the same tool,
  so a number an analyst reads on a chart is the number an agent is arguing about.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.api.session import DEMO_SOURCE, DataSource, Session, get_session
from backend.api.session import bind as bind_session
from backend.api.session import clear as clear_session
from backend.tools.fixtures import FixtureToolset
from backend.tools.results import (
    ArAgingSummary,
    CapabilityManifest,
    DriverAssumptions,
    ForecastSummary,
    LiquidityPosition,
    PolicyConstraints,
)
from backend.tools.tenant import TenantToolset
from backend.tools.toolset import ToolError

router = APIRouter(prefix="/api/data-source", tags=["data-source"])

SessionDep = Annotated[Session, Depends(get_session)]


class DataSourceStatus(BaseModel):
    """`source: null` is the whole empty state. The UI branches on exactly this."""

    source: DataSource | None = None
    company: str | None = None
    as_of: str | None = None
    provider: str
    #: Which downstream steps have actually happened for this source, so a screen can say
    #: "nothing has run yet" rather than rendering an empty grid that looks like a bug.
    has_cycle: bool = False
    has_investigation: bool = False
    has_recommendation: bool = False
    agent_runs: int = 0


class PolicyUpdate(BaseModel):
    """The two thresholds a treasurer actually sets, in minor units of their currency."""

    min_unrestricted_cash_minor: Annotated[int, Field(ge=0)]
    min_30d_liquidity_minor: Annotated[int, Field(ge=0)]


class Position(BaseModel):
    """Everything the Forecast screen needs, in one call, straight off the tool layer.

    A tool that this tenant cannot answer is reported by name in `unavailable` with the
    reason it gave, rather than being silently omitted or faked into an empty list.
    """

    source: DataSource
    liquidity: LiquidityPosition | None = None
    forecast: ForecastSummary | None = None
    aging: ArAgingSummary | None = None
    assumptions: DriverAssumptions | None = None
    constraints: PolicyConstraints | None = None
    capabilities: CapabilityManifest | None = None
    unavailable: dict[str, str] = {}


def _status(session: Session) -> DataSourceStatus:
    return DataSourceStatus(
        source=session.data_source,
        company=session.company if session.data_source else None,
        as_of=session.as_of if session.data_source else None,
        provider=type(session.provider).__name__,
        has_cycle=session.cycle_result is not None,
        has_investigation=session.investigation is not None,
        has_recommendation=(
            session.investigation is not None and session.investigation.recommendation is not None
        ),
        agent_runs=len(session.runs.all()),
    )


@router.get("")
async def current(session: SessionDep) -> DataSourceStatus:
    return _status(session)


@router.post("/demo")
async def use_demo() -> DataSourceStatus:
    """Load the recorded NovaTech ledger — the deterministic golden path, clearly labelled.

    It goes through the same bind as a real tenant, so choosing it also clears whatever the
    previous source produced. The banner and the `kind: "demo"` field are what keep it
    distinguishable from a loaded tenant; nothing else about the plumbing differs.
    """
    source = DEMO_SOURCE.model_copy(update={"loaded_at": datetime.now(UTC)})
    session = bind_session(
        toolset=FixtureToolset(),
        data_source=source,
        company=source.company,
        as_of=source.as_of,
    )
    return _status(session)


@router.post("/clear")
async def clear() -> DataSourceStatus:
    """Unload. Cycle, runs, investigation, cards and audit go with the source."""
    return _status(clear_session())


@router.get("/position")
async def position(session: SessionDep) -> Position:
    """The shared position. 409 when nothing is loaded, because there is no position."""
    if session.data_source is None:
        raise HTTPException(
            status_code=409,
            detail="no data source is loaded; choose one on the Data Source screen",
        )

    unavailable: dict[str, str] = {}

    async def attempt(name: str, call: Any) -> Any:
        try:
            return await call()
        except ToolError as exc:
            unavailable[name] = str(exc)
            return None

    toolset = session.toolset
    return Position(
        source=session.data_source,
        liquidity=await attempt("get_liquidity_position", toolset.get_liquidity_position),
        forecast=await attempt("get_forecast_summary", toolset.get_forecast_summary),
        aging=await attempt("get_ar_aging_summary", toolset.get_ar_aging_summary),
        assumptions=await attempt("list_driver_assumptions", toolset.list_driver_assumptions),
        constraints=await attempt("get_policy_constraints", toolset.get_policy_constraints),
        capabilities=await attempt("get_capability_manifest", toolset.get_capability_manifest),
        unavailable=unavailable,
    )


@router.post("/policy")
async def set_policy(body: PolicyUpdate, session: SessionDep) -> Position:
    """Set this tenant's liquidity floor.

    The shipped `config/treasury_policy.yaml` is NovaTech's, and NovaTech is a $450M
    company. Holding a freight company with a $12M book to a $15M floor would either
    scream breach or never breach, and in both cases the number on screen would be
    somebody else's. So the floor is a per-source setting, as the policy has always been
    documented to be — configurable, versioned, and never hard-coded in an engine.

    Changing it invalidates the cycle that was run against the old one, so the cycle,
    the investigation and the approval cards are cleared with it.
    """
    if not isinstance(session.toolset, TenantToolset):
        raise HTTPException(
            status_code=409,
            detail=("the recorded demo ledger carries its own policy; load a tenant to set one"),
        )
    toolset = session.toolset
    currency = toolset.currency
    updated = toolset.policy.model_copy(
        update={
            "version": toolset.policy.version + 1,
            "min_unrestricted_cash": toolset.policy.min_unrestricted_cash.model_copy(
                update={"amount": body.min_unrestricted_cash_minor, "currency": currency}
            ),
            "min_30d_liquidity": toolset.policy.min_30d_liquidity.model_copy(
                update={"amount": body.min_30d_liquidity_minor, "currency": currency}
            ),
        }
    )
    toolset.policy = updated
    assert session.data_source is not None
    rebound = bind_session(
        toolset=toolset,
        data_source=session.data_source,
        company=session.company,
        as_of=session.as_of,
    )
    return await position(rebound)
