"""The one place the running product's state lives.

The orchestrator modules are all deliberately stateless: `WeeklyCycle`, `Investigation`
and `prepare()` each take what they need and hand back a frozen result. That is right for
testing them, and it leaves exactly one thing undecided -- who holds the result between
two HTTP requests. This does.

It is in-memory and single-tenant, and that is a stated limitation rather than an
oversight. The durable home for a published version, an override and an audit entry is
the persistence layer; persisting them here would duplicate that schema from the wrong
side of the boundary. What this must get right is the *ordering*, because the ordering is
the product:

    cycle.run()  ->  review  ->  publish  ->  policy check  ->  [war room]  ->  approvals

Every gate in that line is enforced here rather than trusted to the caller. You cannot
publish a cycle that has not run, check policy against an unpublished version, open a war
room without a breach, or approve a card that was never prepared. A UI is free to be
wrong about the order; the session is not.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from backend.agents.factory import build_provider
from backend.agents.provider import LLMProvider
from backend.agents.replay import ReplayProvider
from backend.agents.routing import ModelRouting, load_routing
from backend.agents.runner import AgentRunner
from backend.agents.specialists import SPECIALISTS
from backend.agents.specialists import build as build_specialist
from backend.contracts.agent import AgentRole, AgentRun, ProposedAction
from backend.contracts.approvals import (
    ApprovalDecision,
    ApprovalRole,
    ApprovalState,
    Override,
)
from backend.contracts.strategy import Recommendation, WorklistItem
from backend.finance.controls import ApprovalRequiredError
from backend.orchestrator.approvals import (
    ApprovalLedger,
    ApprovalPack,
    AuditEntry,
    AuditLog,
    DryRunAdapter,
    Executor,
    load_matrix,
    prepare,
)
from backend.orchestrator.bus import EventBus
from backend.orchestrator.cycle import CycleResult, WeeklyCycle
from backend.orchestrator.investigation import Investigation, InvestigationResult
from backend.orchestrator.policy import PolicyCheck
from backend.orchestrator.review import ClosedPeriodError, PublishedVersion, ReviewLedger
from backend.orchestrator.runs import InMemoryRunStore
from backend.tools.fixtures import FixtureToolset
from backend.tools.toolset import Toolset

APP_VERSION = "0.1.0"

# The demo tenant and the Monday it runs. Both are fixed because the fixture toolset is:
# a session that let you pick an arbitrary date would be offering a choice the data behind
# it cannot honour.
COMPANY = "NovaTech Industries"
AS_OF = "2026-03-02"

PREPARED_BY = "analyst@novatech"


class DataSource(BaseModel):
    """What the screens are currently showing, and where it came from.

    Every screen in this product is a view of one data source. Before a source is chosen
    there is nothing to view -- not an empty NovaTech, not last week's numbers, nothing --
    and the moment a new one is loaded the previous one's cycle, runs, investigation and
    approval cards go with it. That rule is enforced in `bind_*` below rather than trusted
    to whoever calls it, because a stale figure surviving an onboarding is exactly the kind
    of bug that ends up in a board pack.
    """

    kind: Literal["tenant", "demo"]
    tenant_id: str
    company: str
    currency: str
    as_of: str
    loaded_at: datetime
    #: Rows per entity, read back from the target database rather than echoed from the load
    #: report -- the report says what the loader believed it wrote.
    counts: dict[str, int] = Field(default_factory=dict)
    mapping_path: str | None = None
    reconciled: bool | None = None
    #: Sources this tenant actually has, and the ones it does not, with the reason.
    available_sources: list[str] = Field(default_factory=list)
    missing_sources: list[str] = Field(default_factory=list)
    source_notes: dict[str, str] = Field(default_factory=dict)
    note: str = ""


DEMO_SOURCE = DataSource(
    kind="demo",
    tenant_id="novatech",
    company=COMPANY,
    currency="USD",
    as_of=AS_OF,
    loaded_at=datetime(2026, 3, 2, tzinfo=UTC),
    note=(
        "Recorded NovaTech demo ledger. Synthetic, deterministic, and the fixture the "
        "golden path is asserted against — not a tenant's data."
    ),
    available_sources=["ar_ledger", "ap_ledger", "bank", "dodo", "debt", "forecast", "policy"],
)


class SequenceError(RuntimeError):
    """A step was asked for out of order. The message says which step is missing."""


class Session:
    """One tenant's Monday, from the refresh through to the signed approvals."""

    def __init__(
        self,
        *,
        toolset: Toolset | None = None,
        provider: LLMProvider | None = None,
        routing: ModelRouting | None = None,
        company: str = COMPANY,
        as_of: str = AS_OF,
        prepared_by: str = PREPARED_BY,
        app_version: str = APP_VERSION,
        data_source: DataSource | None = DEMO_SOURCE,
    ) -> None:
        self.company = company
        self.as_of = as_of
        self.prepared_by = prepared_by
        # `None` means "no source is loaded"; every screen renders its empty state and the
        # agent roster refuses to start anything. See `bind_tenant` / `bind_demo`.
        self.data_source = data_source

        self.toolset: Toolset = toolset or FixtureToolset()
        # Constructors and tests stay on fixtures. The API process opts into Gemini via
        # get_session() -> build_provider(), never by silently reaching the network here.
        self.provider: LLMProvider = provider or ReplayProvider()
        self.routing = routing or load_routing()
        self.bus = EventBus()
        self.runs = InMemoryRunStore()

        self.review = ReviewLedger(prepared_by=prepared_by)
        self.audit = AuditLog(app_version)
        self.approvals = ApprovalLedger(audit=self.audit)
        self.executor = Executor(ledger=self.approvals, adapter=DryRunAdapter())
        self.matrix = load_matrix()

        self._cycle: WeeklyCycle | None = None
        self.cycle_result: CycleResult | None = None
        self.policy: PolicyCheck | None = None
        self.investigation: InvestigationResult | None = None
        self.pack: ApprovalPack | None = None

        # Runs an analyst started by hand, kept apart from the cycle's own runs so
        # the Agents screen can show "what I started" without the wave drowning it.
        self.manual_runs: list[AgentRun] = []
        self._manual_seq = 0

        # Every mutating step runs under this. Two browser tabs both pressing "Run cycle"
        # is not a hypothetical, and a half-run cycle interleaved with a war room is a
        # much worse bug than a request that waits.
        self._lock = asyncio.Lock()

    # --- running one agent on its own -------------------------------------------------

    async def run_agent(self, role: AgentRole, *, task: str | None = None) -> AgentRun:
        """Start a single specialist, outside the wave.

        The weekly cycle and the war room decide for themselves which agents to run;
        this is the other thing an analyst wants, which is to point one agent at the
        current state and see what it says. It is the same runtime, the same tool
        allowlist and the same evidence validator — only the trigger is different, so
        an agent started by hand cannot cite something an agent started by the Commander
        could not.

        Supplier Risk is handed the live AP proposals when an investigation has produced
        any, because an adversarial agent with nothing to be adversarial about returns a
        true but useless answer.
        """
        async with self._lock:
            self.require_data_source()
            if role not in SPECIALISTS:
                raise SequenceError(
                    f"{role.value} is not a specialist that can be started on its own; "
                    f"the Commander runs the coordinating roles"
                )
            options: dict[str, object] = {}
            if task:
                options["task"] = task
            if role is AgentRole.SUPPLIER_RISK:
                options["proposals"] = self._ap_proposals()
            if role is AgentRole.VARIANCE and self.cycle_result is not None:
                options["week_ending"] = self.cycle_result.bridge_week_ending

            runner = AgentRunner(
                provider=self.provider,
                toolset=self.toolset,
                routing=self.routing,
                bus=self.bus,
                company=self.company,
                as_of=self.as_of,
                investigation_id=(
                    self.investigation.investigation_id if self.investigation else None
                ),
            )
            self._manual_seq += 1
            run = await runner.run(
                build_specialist(role, **options),
                run_id=f"manual-{role.value}-{self._manual_seq}",
            )
            self.runs.add(run)
            self.manual_runs.append(run)
            return run

    def _ap_proposals(self) -> list[ProposedAction]:
        """The AP agent's live deferral proposals — from the investigation, or from a run
        an analyst started by hand.

        The investigation's own proposals win when there is one, because those are the
        ones the recommendation will be built from. Falling back to the most recent manual
        AP run is what makes the dependency on the Agents screen real: press AP
        Optimisation, then press Supplier Risk, and the second agent is challenging the
        first agent's actual proposals rather than reporting that it had nothing to do.
        """
        if self.investigation is not None:
            for run in self.runs.all(investigation_id=self.investigation.investigation_id):
                if run.agent is AgentRole.AP_OPTIMIZATION and run.finding is not None:
                    return list(run.finding.recommended_actions)
        for run in reversed(self.runs.all()):
            if run.agent is AgentRole.AP_OPTIMIZATION and run.finding is not None:
                return list(run.finding.recommended_actions)
        return []

    # --- steps 1-7 --------------------------------------------------------------------

    def require_data_source(self) -> DataSource:
        if self.data_source is None:
            raise SequenceError(
                "no data source is loaded; connect one on the Data Source screen first. "
                "Nothing in this product is shown against a tenant that has not been loaded"
            )
        return self.data_source

    async def run_cycle(self) -> CycleResult:
        """The automated half of Monday. Idempotent: re-running replaces the draft."""
        async with self._lock:
            self.require_data_source()
            if self.review.published is not None:
                raise SequenceError(
                    f"version {self.review.published.version_id} is published; "
                    "re-running the cycle would rewrite a version that has been signed"
                )
            runner = AgentRunner(
                provider=self.provider,
                toolset=self.toolset,
                routing=self.routing,
                bus=self.bus,
                company=self.company,
                as_of=self.as_of,
            )
            cycle = WeeklyCycle(
                toolset=self.toolset,
                bus=self.bus,
                company=self.company,
                as_of=self.as_of,
                runner=runner,
                store=self.runs,
                cycle_id=f"cycle-{self.as_of}",
            )
            self._cycle = cycle
            self.cycle_result = await cycle.run()
            return self.cycle_result

    def require_cycle(self) -> CycleResult:
        if self.cycle_result is None:
            raise SequenceError("no cycle has been run yet; POST /api/cycle/run first")
        return self.cycle_result

    # --- step 8: review and challenge -------------------------------------------------

    def record_override(self, override: Override, *, effective_on: date | None = None) -> Override:
        """A human replacing a generated assumption, with a stated reason."""
        self.require_cycle()
        try:
            return self.review.record_override(override, effective_on=effective_on)
        except ClosedPeriodError:
            raise  # a closed period is a control refusing, not a step out of order
        except RuntimeError as exc:
            raise SequenceError(str(exc)) from exc

    # --- step 9: publish --------------------------------------------------------------

    def publish(
        self,
        *,
        published_by: str,
        published_by_role: ApprovalRole,
        note: str = "",
        now: datetime | None = None,
    ) -> PublishedVersion:
        """Lock the version. It becomes next week's baseline."""
        result = self.require_cycle()
        try:
            return self.review.publish(
                version_id=result.forecast_version_id,
                week_ending=date.fromisoformat(result.bridge_week_ending),
                published_by=published_by,
                published_by_role=published_by_role,
                note=note,
                now=now or datetime.now(UTC),
            )
        except RuntimeError as exc:
            # "already published" is the session being at the wrong step, not a crash.
            # `SegregationOfDutiesError` subclasses ValueError and passes through.
            raise SequenceError(str(exc)) from exc

    # --- step 10: policy check --------------------------------------------------------

    async def check_policy(self) -> PolicyCheck:
        """Run against the version the Treasurer actually published, never a draft."""
        async with self._lock:
            result = self.require_cycle()
            assert self._cycle is not None  # set together with cycle_result
            if self.review.published is None:
                raise SequenceError(
                    "the policy check runs against a published version; publish first"
                )
            self.policy = await self._cycle.policy_check(result, self.review)
            return self.policy

    # --- the escalation branch --------------------------------------------------------

    async def open_war_room(self) -> InvestigationResult:
        """Open on the specific, dated, quantified breach step 10 found. Never a vibe."""
        async with self._lock:
            if self.policy is None:
                raise SequenceError(
                    "a war room opens on a policy check; run the check before opening one"
                )
            if not self.policy.escalate:
                raise SequenceError(
                    "the published version is within policy; there is nothing to escalate. "
                    "A war room opened without a breach is a war room nobody trusts"
                )
            investigation = Investigation(
                provider=self.provider,
                toolset=self.toolset,
                routing=self.routing,
                bus=self.bus,
                company=self.company,
                as_of=date.fromisoformat(self.as_of),
                check=self.policy,
                store=self.runs,
            )
            self.investigation = await investigation.run()
            self.pack = None  # the cards belong to this recommendation, not the last one
            return self.investigation

    def require_recommendation(self) -> Recommendation:
        if self.investigation is None or self.investigation.recommendation is None:
            raise SequenceError("no investigation has produced a recommendation yet")
        return self.investigation.recommendation

    # --- approvals --------------------------------------------------------------------

    async def approval_pack(self) -> ApprovalPack:
        """Classify, route and build the cards. Cached: the cards carry stable ids."""
        async with self._lock:
            if self.pack is not None:
                return self.pack
            recommendation = self.require_recommendation()
            candidates = await self.toolset.rank_deferral_candidates()
            published = self.review.published
            self.pack = prepare(
                recommendation,
                candidates=candidates,
                matrix=self.matrix,
                prepared_by=self.prepared_by,
                data_snapshot_ref=published.version_id if published else None,
            )
            self.approvals.register(self.pack.requests)
            return self.pack

    def decide(self, decision: ApprovalDecision) -> AuditEntry:
        """Apply a decision. Maker-checker, routing and blocked routes all raise here."""
        if self.pack is None:
            raise SequenceError("no approval cards have been prepared yet")
        return self.approvals.decide(decision)

    async def execute(self, seq: int) -> str:
        """Execute one row, through the gate. Dry-run by default and by design."""
        if self.pack is None:
            raise SequenceError("no approval cards have been prepared yet")
        row = self._row(seq)
        return await self.executor.execute(row)

    def _row(self, seq: int) -> WorklistItem:
        assert self.pack is not None
        row = next((item for item in self.pack.worklist if item.seq == seq), None)
        if row is None:
            raise ApprovalRequiredError(f"no worklist row {seq}")
        return row

    def state_of(self, request_id: str) -> ApprovalState:
        return self.approvals.state_of(request_id)


_session: Session | None = None


def get_session() -> Session:
    """The process-wide session. A FastAPI dependency, and overridable in tests."""
    global _session
    if _session is None:
        # The API process starts with nothing loaded. A bare `Session()` is the recorded
        # demo ledger and is right for tests and `make demo`; a browser must choose.
        _session = Session(provider=build_provider(), data_source=None)
    return _session


def _release(previous: Session | None, replacement: Session) -> None:
    """Close the outgoing toolset's database session, unless it is being kept.

    `TenantToolset` holds one read connection for its lifetime so an agent's six tool
    calls see one consistent snapshot. Dropping the object without closing that connection
    leaks it, which shows up as an unraisable `ResourceWarning` in whichever unrelated test
    the collector happens to be inside when it fires.
    """
    if previous is None or previous.toolset is replacement.toolset:
        return
    closer = getattr(previous.toolset, "close", None)
    if callable(closer):
        closer()


def reset_session(session: Session | None = None) -> Session:
    """Start Monday again. Used by the demo script and by the e2e tests.

    Bare `reset_session()` keeps ReplayProvider so CI never dials Gemini. Pass an explicit
    `Session(provider=...)` (or call `get_session()` in the API process) for live models.
    """
    global _session
    replacement = session or Session()
    _release(_session, replacement)
    _session = replacement
    return _session


def bind(
    *,
    toolset: Toolset,
    data_source: DataSource,
    company: str,
    as_of: str,
    provider: LLMProvider | None = None,
) -> Session:
    """Point the product at a data source, discarding everything the last one produced.

    This is the whole of the "clear on onboarding" rule: a new `Session` is constructed,
    so the cycle result, the run store, the event bus, the investigation, the approval
    ledger and the audit log are all new objects. There is no partial reset to get wrong,
    and no screen can hold a reference to a figure that outlived its source.
    """
    return reset_session(
        Session(
            toolset=toolset,
            provider=provider or _provider(),
            company=company,
            as_of=as_of,
            data_source=data_source,
        )
    )


def clear() -> Session:
    """Unload the data source. Every screen goes back to its empty state."""
    return reset_session(Session(provider=_provider(), data_source=None))


def _provider() -> LLMProvider:
    """Keep the provider the process is already using.

    Which model backs the agents is a property of the process, not of the tenant, and
    rebuilding it from the environment on every bind would mean a test that carefully
    installed `ReplayProvider` starts dialling Gemini the moment a source is loaded.
    """
    if _session is not None:
        return _session.provider
    return build_provider()
