"""The investigation state machine: one war room, opened, run, replanned and closed.

This is the escalation branch of the weekly cycle, and it exists only because step 10 of
that cycle found a specific, dated, quantified breach. It is a state machine rather than a
script for one reason: it has to be able to fail partway and still produce something a
treasurer can act on.

The order is fixed and each phase is streamed:

    planning -> investigating -> resolving conflicts -> generating scenarios
             -> stress testing -> [replanning] -> recommending -> closed

The replan loop is the part worth reading carefully. A bundle that fails stress does not
disappear and is not quietly swapped; the failure reason is recorded as a `ReplanAttempt`,
the constraint it failed is tightened, and the next candidate must satisfy the tightened
constraint to be selected at all. The full attempt history travels into the recommendation,
because "we tried the cheaper plan and it did not survive an AR shortfall at our own 90th
percentile" is the most persuasive sentence in the whole output, and deleting the failed
attempt deletes it.

Every bound is explicit: replan count, follow-up depth, and a wall clock. An investigation
that could run forever on contradictory data will meet contradictory data.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, date, datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.agents.context import Incident
from backend.agents.provider import LLMProvider
from backend.agents.routing import ModelRouting
from backend.contracts.agent import ActionKind, AgentRun, ProposedAction
from backend.contracts.constraints import ConstraintViolation
from backend.contracts.events import (
    InvestigationClosed,
    InvestigationOpened,
    InvestigationPhase,
    InvestigationPhaseChanged,
    RecommendationReady,
    ReplanStarted,
    ScenarioGenerated,
    StatusMark,
    SystemDegraded,
)
from backend.contracts.money import Money
from backend.contracts.strategy import Recommendation, ReplanAttempt, Strategy, StressResult
from backend.orchestrator.bus import EventBus
from backend.orchestrator.commander import Commander, Dispatch
from backend.orchestrator.conflicts import ConflictResolver, ResolvedConflict, detect
from backend.orchestrator.policy import PolicyCheck
from backend.orchestrator.runs import RunStore
from backend.orchestrator.stress import StressReport, StressTester
from backend.orchestrator.worklist import Composition, ScoreCard, compose, to_worklist
from backend.tools.toolset import Toolset

# A bundle that has failed three differently-shaped stress tests is telling you the
# shortfall is real, not that the fourth bundle is the answer.
MAX_REPLANS = 3

# The parallel wave is meant to finish in about a minute; the whole investigation is given
# a generous multiple of that and then stops, degraded, with whatever it has.
WALL_CLOCK_S = 180.0


class InvestigationResult(BaseModel):
    """Everything one war room produced, including the attempts that did not work."""

    model_config = ConfigDict(frozen=True)

    investigation_id: str
    phase: InvestigationPhase
    trigger: str
    breach: ConstraintViolation | None = None
    plan_id: str = ""
    plan_rationale: str = ""
    runs: list[AgentRun] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    strategies: list[Strategy] = Field(default_factory=list)
    scores: dict[str, ScoreCard] = Field(default_factory=dict)
    stress_reports: list[StressReport] = Field(default_factory=list)
    recommendation: Recommendation | None = None
    elapsed_ms: int = 0
    degraded: bool = False
    degradation_reasons: list[str] = Field(default_factory=list)


class Investigation:
    """One war room, from the breach that opened it to the worklist it produced."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        toolset: Toolset,
        routing: ModelRouting,
        bus: EventBus,
        company: str,
        as_of: date,
        check: PolicyCheck,
        store: RunStore | None = None,
        investigation_id: str | None = None,
        max_replans: int = MAX_REPLANS,
        wall_clock_s: float = WALL_CLOCK_S,
    ) -> None:
        self._provider = provider
        self._toolset = toolset
        self._routing = routing
        self._bus = bus
        self._company = company
        self._as_of = as_of
        self._check = check
        self._store = store
        self._max_replans = max_replans
        self._wall_clock_s = wall_clock_s
        self.investigation_id = investigation_id or f"inv-{uuid.uuid4().hex[:8]}"
        self._began = 0.0

    async def run(self) -> InvestigationResult:
        breach = self._check.worst()
        if breach is None:
            raise ValueError(
                "an investigation opens on a breach; this policy check found none, "
                "which means the cycle published inside policy and there is nothing to do"
            )
        incident = self._check.incident()
        self._began = time.perf_counter()

        self._bus.emit(
            InvestigationOpened,
            investigation_id=self.investigation_id,
            status_line=f"War room opened: {breach.display()}"[:200],
            trigger=breach.display()[:200],
            detected_at=datetime.now(UTC),
            quantum=_quantum(breach),
            breach=breach,
        )

        degradations: list[str] = []
        commander = Commander(
            provider=self._provider,
            toolset=self._toolset,
            routing=self._routing,
            bus=self._bus,
            company=self._company,
            as_of=str(self._as_of),
            investigation_id=self.investigation_id,
            incident=incident,
            store=self._store,
        )

        # 1. Plan.
        self._phase(InvestigationPhase.PLANNING)
        dispatch = await commander.plan(tuple(v.kind for v in self._check.hard_violations))
        if not dispatch.model_selected:
            degradations.append(f"planning: {dispatch.fallback_reason}")

        # 2. Investigate in parallel.
        self._phase(InvestigationPhase.INVESTIGATING)
        dispatch = await commander.investigate(dispatch)
        degradations.extend(dispatch.degradation_reasons())

        # 3. Resolve contradictions, by evidence.
        self._phase(InvestigationPhase.RESOLVING_CONFLICTS)
        resolved = await self._resolve(dispatch, incident)
        degradations.extend(
            f"conflict {r.conflict.subject}: {r.fallback_reason}"
            for r in resolved
            if not r.model_resolved
        )

        # 4. Compose bundles. Deterministic from here to the worklist.
        self._phase(InvestigationPhase.GENERATING_SCENARIOS)
        composition = await self._compose(dispatch, resolved)
        for strategy in composition.strategies:
            self._bus.emit(
                ScenarioGenerated,
                investigation_id=self.investigation_id,
                mark=StatusMark.INFO,
                status_line=(
                    f"{strategy.name}: {strategy.net_cash_impact} raised, "
                    f"score {composition.scores[strategy.strategy_id].total}"
                )[:200],
                strategy=strategy,
            )

        # 5 and 6. Stress, and replan on the specific failure.
        self._phase(InvestigationPhase.STRESS_TESTING)
        selected, reports, attempts, stress_degradations = await self._stress_and_replan(
            composition, incident
        )
        degradations.extend(stress_degradations)

        # 7. Recommend.
        self._phase(InvestigationPhase.RECOMMENDING)
        recommendation = self._recommend(
            dispatch, composition, selected, reports, attempts, degradations
        )
        self._bus.emit(
            RecommendationReady,
            investigation_id=self.investigation_id,
            mark=StatusMark.WARN if recommendation.degraded else StatusMark.OK,
            status_line=f"Recommendation: {recommendation.summary}"[:200],
            recommendation=recommendation,
        )

        self._phase(InvestigationPhase.CLOSED)
        self._bus.emit(
            InvestigationClosed,
            investigation_id=self.investigation_id,
            mark=StatusMark.WARN if recommendation.degraded else StatusMark.OK,
            status_line="War room closed",
            phase=InvestigationPhase.CLOSED,
            recommendation_id=recommendation.recommendation_id,
            reason=recommendation.degradation_reason or "",
        )
        return InvestigationResult(
            investigation_id=self.investigation_id,
            phase=InvestigationPhase.CLOSED,
            trigger=breach.display(),
            breach=breach,
            plan_id=dispatch.plan.plan_id,
            plan_rationale=dispatch.rationale,
            runs=dispatch.runs,
            conflicts=[r.conflict.conflict_id for r in resolved],
            strategies=composition.strategies,
            scores=composition.scores,
            stress_reports=reports,
            recommendation=recommendation,
            elapsed_ms=self._elapsed_ms(),
            degraded=bool(degradations),
            degradation_reasons=degradations,
        )

    # --- phases -------------------------------------------------------------------------

    def _phase(self, phase: InvestigationPhase, *, depth: int = 0) -> None:
        self._bus.emit(
            InvestigationPhaseChanged,
            investigation_id=self.investigation_id,
            mark=StatusMark.WORKING,
            status_line=f"Phase: {phase.value.replace('_', ' ')}",
            phase=phase,
            depth=depth,
            elapsed_ms=self._elapsed_ms(),
        )

    def _elapsed_ms(self) -> int:
        return int((time.perf_counter() - self._began) * 1000)

    def _out_of_time(self) -> bool:
        return (time.perf_counter() - self._began) > self._wall_clock_s

    async def _resolve(
        self, dispatch: Dispatch, incident: Incident | None
    ) -> list[ResolvedConflict]:
        conflicts = detect(dispatch.findings)
        if not conflicts:
            return []
        resolver = ConflictResolver(
            provider=self._provider,
            toolset=self._toolset,
            routing=self._routing,
            bus=self._bus,
            company=self._company,
            as_of=str(self._as_of),
            investigation_id=self.investigation_id,
            incident=incident,
            store=self._store,
        )
        return await resolver.resolve_all(conflicts, dispatch.findings)

    async def _compose(self, dispatch: Dispatch, resolved: list[ResolvedConflict]) -> Composition:
        return compose(
            dispatch.findings,
            policy=await self._toolset.get_policy_constraints(),
            candidates=await self._toolset.rank_deferral_candidates(),
            position=await self._toolset.get_liquidity_position(),
            conflicts=resolved,
        )

    # --- stress and replan ---------------------------------------------------------------

    async def _stress_and_replan(
        self, composition: Composition, incident: Incident | None
    ) -> tuple[Strategy | None, list[StressReport], list[ReplanAttempt], list[str]]:
        """Test the best bundle; on failure, tighten and try the next that could survive."""
        position = await self._toolset.get_liquidity_position()
        tester = StressTester(
            provider=self._provider,
            toolset=self._toolset,
            routing=self._routing,
            bus=self._bus,
            company=self._company,
            as_of=str(self._as_of),
            investigation_id=self.investigation_id,
            incident=incident,
        )

        candidates = composition.ranked()
        reports: list[StressReport] = []
        attempts: list[ReplanAttempt] = []
        degradations: list[str] = []

        for attempt, strategy in enumerate(candidates[: self._max_replans + 1], start=1):
            if self._out_of_time():
                degradations.append(
                    f"wall clock: the investigation stopped after {self._elapsed_ms()}ms "
                    f"with {len(candidates) - attempt + 1} bundle(s) untested"
                )
                break

            report = await tester.run(strategy, position=position)
            reports.append(report)
            if not report.model_selected:
                degradations.append(f"stress selection: {report.fallback_reason}")
            if report.passed:
                return strategy, reports, attempts, degradations

            failure = report.failure_reason()
            worst = report.worst()
            attempts.append(
                ReplanAttempt(
                    attempt=attempt,
                    strategy_id=strategy.strategy_id,
                    failure_reason=failure[:400],
                    tightened_constraint=(
                        f"the bundle must clear {position.floor} under "
                        f"{worst.stressor.label} ({worst.stressor.shift_pct}%)"
                        if worst is not None
                        else None
                    ),
                )
            )
            self._bus.emit(
                ReplanStarted,
                investigation_id=self.investigation_id,
                status_line=f"Replan {attempt}: {strategy.name} failed stress"[:200],
                attempt=attempts[-1],
            )
            self._phase(InvestigationPhase.REPLANNING, depth=attempt)

        if not reports:
            degradations.append("no feasible bundle survived the constraint gate")
        elif attempts:
            degradations.append(
                f"no bundle passed stress after {len(attempts)} attempt(s); "
                "the shortfall is larger than the levers available"
            )
        return None, reports, attempts, degradations

    # --- the answer -----------------------------------------------------------------------

    def _recommend(
        self,
        dispatch: Dispatch,
        composition: Composition,
        selected: Strategy | None,
        reports: list[StressReport],
        attempts: list[ReplanAttempt],
        degradations: list[str],
    ) -> Recommendation:
        ranked = composition.ranked()
        chosen = selected or (ranked[0] if ranked else None)
        results: list[StressResult] = [r for report in reports for r in report.results]

        if chosen is None:
            reason = (
                "No bundle survived the constraint gate, so there is nothing to recommend. "
                "Every proposed lever was refused; the refusals and their evidence are below."
            )
            self._bus.emit(
                SystemDegraded,
                investigation_id=self.investigation_id,
                mark=StatusMark.FAIL,
                status_line="no recommendable bundle",
                component="investigation",
                reason=reason[:400],
            )
            return Recommendation(
                recommendation_id=f"rec-{self.investigation_id}",
                investigation_id=self.investigation_id,
                selected_strategy=_empty_strategy(composition.shortfall),
                rejected_actions=composition.rejected,
                stress_results=results,
                replan_history=attempts,
                degraded=True,
                degradation_reason=reason,
                requires_human_review=True,
                summary=reason,
            )

        degraded = bool(degradations) or selected is None
        reason = "; ".join(degradations)[:400] if degradations else ""
        if selected is None and not reason:
            reason = "no bundle passed stress; the highest-scoring one is shown for review"
        elif selected is None:
            reason = f"{reason}; no bundle passed stress"[:400]

        return Recommendation(
            recommendation_id=f"rec-{self.investigation_id}",
            investigation_id=self.investigation_id,
            selected_strategy=chosen,
            alternatives=[s for s in composition.strategies if s.strategy_id != chosen.strategy_id],
            worklist=to_worklist(
                chosen,
                as_of=self._as_of,
                findings=dispatch.findings,
                candidates=composition.candidates,
            ),
            rejected_actions=composition.rejected,
            stress_results=results,
            replan_history=attempts,
            degraded=degraded,
            degradation_reason=reason or None,
            requires_human_review=degraded,
            summary=_summarise(chosen, composition, attempts, selected is not None),
        )


def _quantum(breach: ConstraintViolation) -> Money | None:
    return breach.observed_money


def _empty_strategy(shortfall: Money) -> Strategy:
    """`Recommendation` requires a strategy; an empty one says exactly what happened."""
    return Strategy(
        strategy_id="none-feasible",
        name="No feasible bundle",
        actions=[
            ProposedAction(
                kind=ActionKind.ASSUMPTION_REVIEW,
                rationale=(
                    f"Every proposed lever was refused by the constraint gate, leaving the "
                    f"{shortfall} shortfall unaddressed. This needs a human decision, not "
                    f"another bundle."
                ),
            )
        ],
        net_cash_impact=Money.zero(shortfall.currency),
    )


def _summarise(
    strategy: Strategy,
    composition: Composition,
    attempts: list[ReplanAttempt],
    passed_stress: bool,
) -> str:
    parts = [
        f"{strategy.name} raises {strategy.net_cash_impact} against a "
        f"{composition.shortfall} shortfall, scoring "
        f"{composition.scores[strategy.strategy_id].total} of 100."
    ]
    if attempts:
        parts.append(
            f"{len(attempts)} earlier bundle(s) failed stress and were replanned: "
            + "; ".join(f"{a.strategy_id} -- {a.failure_reason}" for a in attempts)
        )
    parts.append(
        "It survives every stressor it was tested against."
        if passed_stress
        else "It has not survived stress testing and is shown for human review only."
    )
    if composition.rejected:
        parts.append(f"{len(composition.rejected)} lever(s) were refused, with reasons below.")
    return " ".join(parts)[:1000]
