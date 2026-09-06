"""The one place the running product's state lives.

The orchestrator modules are all deliberately stateless: `WeeklyCycle`, `Investigation`
and `prepare()` each take what they need and hand back a frozen result. That is right for
testing them, and it leaves exactly one thing undecided -- who holds the result between
two HTTP requests. This does.

It is in-memory and single-tenant, and that is a stated limitation rather than an
oversight. The durable home for a published version, an override and an audit entry is
Person 1's tables; persisting them here would be inventing their schema from the wrong
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

from backend.agents.fake import FakeProvider
from backend.agents.provider import LLMProvider
from backend.agents.routing import ModelRouting, load_routing
from backend.agents.runner import AgentRunner
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
    ) -> None:
        self.company = company
        self.as_of = as_of
        self.prepared_by = prepared_by

        self.toolset: Toolset = toolset or FixtureToolset()
        self.provider: LLMProvider = provider or FakeProvider()
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

        # Every mutating step runs under this. Two browser tabs both pressing "Run cycle"
        # is not a hypothetical, and a half-run cycle interleaved with a war room is a
        # much worse bug than a request that waits.
        self._lock = asyncio.Lock()

    # --- steps 1-7 --------------------------------------------------------------------

    async def run_cycle(self) -> CycleResult:
        """The automated half of Monday. Idempotent: re-running replaces the draft."""
        async with self._lock:
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
        _session = Session()
    return _session


def reset_session(session: Session | None = None) -> Session:
    """Start Monday again. Used by the demo script and by the e2e tests."""
    global _session
    _session = session or Session()
    return _session
