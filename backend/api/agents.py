"""The agent space: what the specialists are, and starting one on its own.

The war room runs agents in waves the Commander plans. This is the other half of the
product an analyst actually asks for — a room where the six specialists are visible as
things with names, jobs and tools, and where you can point one of them at the current
state and watch it work.

Starting an agent here is not a shortcut past anything. It goes through `AgentRunner`
like every other run: same tool allowlist, same context budget, same evidence
validator, same `AgentRun` record on the same event bus. So a finding produced by a
button on this screen is exactly as citable — and exactly as rejectable — as one the
Commander asked for, and it appears in the War Room stream alongside them.

What this module adds is only the roster: a human-readable description of each agent,
what it may call, what model it is routed to, and what it last said.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.api.session import DataSource, SequenceError, Session, get_session
from backend.contracts.agent import AgentRole, AgentRun
from backend.contracts.provenance import SourceSystem
from backend.tools.registry import tools_for

router = APIRouter(prefix="/api/agents", tags=["agents"])

SessionDep = Annotated[Session, Depends(get_session)]

#: What each specialist is for, in the words a CFO would use. These are descriptions of
#: the agent, not prompts for it -- the prompts live in `prompts/` and are the contract
#: with the model. Keeping them apart means editing a screen cannot change a finding.
ROSTER: dict[AgentRole, dict[str, str]] = {
    AgentRole.FORECAST: {
        "title": "Cash Forecast",
        "purpose": "Explains what the 13-week forecast assumes and which drivers have gone stale.",
        "asks": "What is this forecast resting on, and what has aged out?",
    },
    AgentRole.VARIANCE: {
        "title": "Variance",
        "purpose": "Root-causes each material delta in the bridge and says which causes repeat.",
        "asks": "Why did last week miss, and will it miss again?",
    },
    AgentRole.AR_COLLECTIONS: {
        "title": "AR Collections",
        "purpose": "Ranks realistically collectable receivables by payment behaviour and dispute state.",
        "asks": "How much receivable cash can actually be pulled forward?",
    },
    AgentRole.AP_OPTIMIZATION: {
        "title": "AP Optimisation",
        "purpose": "Finds payables that can be deferred inside terms. Payroll and tax are off limits.",
        "asks": "Which payments can safely move, and by how many days?",
    },
    AgentRole.SUPPLIER_RISK: {
        "title": "Supplier Risk",
        "purpose": "Challenges proposed deferrals against supplier criticality and contract terms.",
        "asks": "Which of those deferrals would actually cost us more than they save?",
    },
    AgentRole.DODO_REVENUE: {
        "title": "Dodo Revenue",
        "purpose": "Reads payment decline patterns and estimates collections at risk and recoverable.",
        "asks": "How much of expected collection is failing, and how much comes back?",
    },
}


#: What each agent needs before its answer means anything, and what consumes that answer.
#:
#: These are real dependencies, not presentation. Supplier Risk exists to challenge AP's
#: proposals, so running it first produces a true but empty finding and the card says so
#: rather than letting an analyst read "no objections" as agreement. Variance needs a
#: prior week to compare against. The rest are independent and can be run in any order,
#: which is worth stating too -- a graph that implies false ordering is its own bug.
DEPENDENCIES: dict[AgentRole, dict[str, Any]] = {
    AgentRole.FORECAST: {
        "requires_agents": [],
        "requires_sources": [SourceSystem.AR_LEDGER, SourceSystem.AP_LEDGER, SourceSystem.BANK],
        "feeds": [],
        "note": "Independent. Reads the derived 13-week series and the drivers under it.",
    },
    AgentRole.VARIANCE: {
        "requires_agents": [],
        "requires_sources": [SourceSystem.FORECAST],
        "requires_published_version": True,
        "feeds": [AgentRole.FORECAST],
        "note": (
            "Needs a prior published forecast to compare with actuals. A freshly loaded "
            "tenant has one snapshot and no history, so this agent will report degraded "
            "until a cycle has been published against it."
        ),
    },
    AgentRole.AR_COLLECTIONS: {
        "requires_agents": [],
        "requires_sources": [SourceSystem.AR_LEDGER],
        "feeds": [],
        "note": "Independent. Ranks the tenant's own open receivables.",
    },
    AgentRole.AP_OPTIMIZATION: {
        "requires_agents": [],
        "requires_sources": [SourceSystem.AP_LEDGER],
        "feeds": [AgentRole.SUPPLIER_RISK],
        "note": "Independent, but its proposals are what Supplier Risk exists to challenge.",
    },
    AgentRole.SUPPLIER_RISK: {
        "requires_agents": [AgentRole.AP_OPTIMIZATION],
        "requires_sources": [SourceSystem.AP_LEDGER],
        "feeds": [],
        "note": (
            "Adversarial. Run AP Optimisation first: with no proposals in front of it this "
            "agent has nothing to reject, and an empty objection is not an endorsement."
        ),
    },
    AgentRole.DODO_REVENUE: {
        "requires_agents": [],
        "requires_sources": [SourceSystem.DODO],
        "feeds": [AgentRole.FORECAST],
        "note": (
            "Needs connected Dodo payment events. Without them collections at risk cannot "
            "be estimated and the agent degrades rather than guessing a failure rate."
        ),
    },
}


class Dependency(BaseModel):
    """One edge, with the reason it exists — an arrow nobody can read is decoration."""

    role: AgentRole
    title: str
    satisfied: bool
    reason: str


class AgentCard(BaseModel):
    """One agent, as the roster screen renders it."""

    role: AgentRole
    title: str
    purpose: str
    asks: str
    tools: list[str]
    model: str
    timeout_s: float
    startable: bool
    #: Agents whose output this one reads. Unsatisfied edges do not block the button; they
    #: are stated, because refusing to let a CFO run an agent is worse than telling them
    #: what the answer will be missing.
    depends_on: list[Dependency] = Field(default_factory=list)
    feeds: list[AgentRole] = Field(default_factory=list)
    requires_sources: list[str] = Field(default_factory=list)
    missing_sources: list[str] = Field(default_factory=list)
    dependency_note: str = ""
    ready: bool = True
    blocked_reason: str | None = None
    last_run: AgentRun | None = None


class Roster(BaseModel):
    company: str | None
    as_of: str | None
    provider: str
    source: DataSource | None = None
    agents: list[AgentCard] = Field(default_factory=list)
    manual_runs: list[AgentRun] = Field(default_factory=list)
    #: The graph as edges, so a client can draw it without re-deriving the topology.
    edges: list[dict[str, str]] = Field(default_factory=list)


class StartRequest(BaseModel):
    """An optional task override, so an analyst can ask a narrower question."""

    task: Annotated[str, Field(max_length=400)] | None = None


def _blocked_reason(
    *,
    source: DataSource | None,
    absent: list[str],
    depends: list[Dependency],
    needs_history: bool,
    published: bool,
) -> str | None:
    """Why this agent's answer would be incomplete, in the order a CFO would ask.

    None of these stop the run. An agent that can only answer half the question should
    answer half the question and say which half is missing -- that is what `degraded`
    means -- and a screen that greyed the button out instead would be hiding the more
    useful outcome.
    """
    if source is None:
        return "no data source is loaded"
    unmet = [item for item in depends if not item.satisfied]
    if unmet:
        names = ", ".join(item.title for item in unmet)
        return f"{names} has not run yet; this agent would have nothing to work from"
    if needs_history and not published:
        return (
            "no forecast version has been published for this source yet, so there is no "
            "prior week to compare against; the agent will report degraded"
        )
    if absent:
        return f"this tenant has no {', '.join(absent)}; the agent will run and report degraded"
    return None


def _last_run(session: Session, role: AgentRole) -> AgentRun | None:
    for run in reversed(session.runs.all()):
        if run.agent is role:
            return run
    return None


@router.get("")
async def roster(session: SessionDep) -> Roster:
    """The roster, each card carrying its dependencies and whatever it last concluded."""
    source = session.data_source
    missing = set(source.missing_sources) if source else set()
    ran = {run.agent for run in session.runs.all() if run.finding is not None}

    cards: list[AgentCard] = []
    edges: list[dict[str, str]] = []
    for role, described in ROSTER.items():
        route = session.routing.route(role)
        wiring = DEPENDENCIES[role]
        needs_history = bool(wiring.get("requires_published_version"))
        depends: list[Dependency] = []
        for upstream in wiring["requires_agents"]:
            satisfied = upstream in ran
            depends.append(
                Dependency(
                    role=upstream,
                    title=ROSTER[upstream]["title"],
                    satisfied=satisfied,
                    reason=(
                        f"{ROSTER[upstream]['title']} has produced a finding this session"
                        if satisfied
                        else f"{ROSTER[upstream]['title']} has not run yet"
                    ),
                )
            )
            edges.append({"from": upstream.value, "to": role.value, "kind": "agent"})
        for downstream in wiring["feeds"]:
            edges.append({"from": role.value, "to": downstream.value, "kind": "agent"})

        needed = [item.value for item in wiring["requires_sources"]]
        absent = [item for item in needed if item in missing]
        cards.append(
            AgentCard(
                role=role,
                title=described["title"],
                purpose=described["purpose"],
                asks=described["asks"],
                tools=sorted(tools_for(role)),
                model=route.model,
                timeout_s=route.timeout_s,
                startable=source is not None,
                depends_on=depends,
                feeds=list(wiring["feeds"]),
                requires_sources=needed,
                missing_sources=absent,
                dependency_note=wiring["note"],
                ready=(
                    source is not None
                    and not absent
                    and all(d.satisfied for d in depends)
                    and not (needs_history and session.review.published is None)
                ),
                blocked_reason=_blocked_reason(
                    source=source,
                    absent=absent,
                    depends=depends,
                    needs_history=needs_history,
                    published=session.review.published is not None,
                ),
                last_run=_last_run(session, role),
            )
        )
    return Roster(
        company=session.company if source else None,
        as_of=session.as_of if source else None,
        provider=type(session.provider).__name__,
        source=source,
        agents=cards,
        manual_runs=session.manual_runs[-20:],
        # De-duplicated: an edge is declared on both ends so each card can render it, and
        # the graph should carry it once.
        edges=list({(e["from"], e["to"]): e for e in edges}.values()),
    )


@router.post("/{role}/run")
async def start(role: AgentRole, body: StartRequest | None, session: SessionDep) -> AgentRun:
    """Start one agent. Returns its run record — including when the run failed.

    A degraded or refused agent is a 200 carrying that outcome, not an error status: the
    screen needs to render "the AP agent refused, here is why" as a finding, and turning
    a legitimate refusal into an HTTP failure would lose the reason.
    """
    try:
        return await session.run_agent(role, task=body.task if body else None)
    except SequenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/runs")
async def runs(session: SessionDep) -> list[AgentRun]:
    """Every run this session has produced, hand-started and Commander-started alike."""
    return session.runs.all()
