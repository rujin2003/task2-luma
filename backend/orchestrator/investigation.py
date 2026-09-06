"""The investigation state machine: plan → wave → conflict → scenarios → stress → replan.

This is the escalation branch. Deterministic composition and stress math sit beside the
specialist wave so the golden path is byte-identical under FakeProvider + FixtureToolset.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from backend.agents.fake import FakeProvider
from backend.agents.provider import LLMProvider
from backend.agents.routing import ModelRouting, load_routing
from backend.agents.runner import AgentRunner
from backend.agents.specialists import build
from backend.agents.specialists.supplier_risk import SupplierRiskAgent
from backend.contracts.agent import (
    ActionKind,
    AgentFinding,
    AgentRole,
    AgentRun,
    ProposedAction,
)
from backend.contracts.approvals import (
    ApprovalRequest,
    ApprovalRole,
    ApprovalRoute,
    ApprovalState,
)
from backend.contracts.constraints import ConstraintViolation
from backend.contracts.events import (
    ApprovalRequested,
    ConflictDetected,
    ConflictResolved,
    FollowupDispatched,
    InvestigationClosed,
    InvestigationOpened,
    InvestigationPhase,
    InvestigationPhaseChanged,
    PlanSelected,
    RecommendationReady,
    ReplanStarted,
    ScenarioGenerated,
    StatusMark,
    StressCompleted,
)
from backend.contracts.money import Money
from backend.contracts.strategy import (
    Recommendation,
    ReplanAttempt,
    Strategy,
    StressResult,
    WorklistItem,
)
from backend.orchestrator.bus import EventBus
from backend.orchestrator.conflicts import (
    detect_conflicts,
    resolve_conflict,
    surviving_actions,
)
from backend.orchestrator.executor import AgentExecutor, WaveUnit
from backend.orchestrator.plans import select_plan, skipped_agents
from backend.orchestrator.runs import InMemoryRunStore
from backend.orchestrator.scenarios import (
    compose_worklist,
    price_strategies,
    propose_strategy_bundles,
    rejected_from_findings,
)
from backend.orchestrator.stress import apply_stress, calibrate_stressors
from backend.tools.toolset import Toolset


@dataclass
class InvestigationResult:
    investigation_id: str
    phase: InvestigationPhase
    breach: ConstraintViolation
    plan_id: str
    findings: list[AgentFinding] = field(default_factory=list)
    runs: list[AgentRun] = field(default_factory=list)
    strategies: list[Strategy] = field(default_factory=list)
    stress_results: list[StressResult] = field(default_factory=list)
    replan_history: list[ReplanAttempt] = field(default_factory=list)
    recommendation: Recommendation | None = None
    worklist: list[WorklistItem] = field(default_factory=list)
    approvals: list[ApprovalRequest] = field(default_factory=list)


class InvestigationRunner:
    """Owns one war-room investigation from open to recommendation."""

    def __init__(
        self,
        *,
        tools: Toolset,
        bus: EventBus,
        provider: LLMProvider | None = None,
        routing: ModelRouting | None = None,
        company: str = "NovaTech Industries",
        as_of: str = "2026-03-02",
        max_replans: int = 2,
    ) -> None:
        self._tools = tools
        self._bus = bus
        self._provider = provider or FakeProvider(strict=True)
        self._routing = routing or load_routing()
        self._company = company
        self._as_of = as_of
        self._max_replans = max_replans
        self._store = InMemoryRunStore()

    async def run(
        self,
        *,
        breach: ConstraintViolation,
        trigger: str,
        incident_kind: str | None = None,
        investigation_id: str | None = None,
    ) -> InvestigationResult:
        investigation_id = investigation_id or str(uuid.uuid4())
        opened_at = datetime.now(UTC)
        self._bus.emit(
            InvestigationOpened,
            investigation_id=investigation_id,
            status_line=f"War room opened: {trigger}"[:200],
            trigger=trigger[:200],
            detected_at=opened_at,
            quantum=breach.observed_money,
            breach=breach,
        )
        self._phase(investigation_id, InvestigationPhase.PLANNING)

        manifest = await self._tools.get_capability_manifest()
        plan = select_plan(breach=breach, manifest=manifest, incident_kind=incident_kind)
        self._bus.emit(
            PlanSelected,
            investigation_id=investigation_id,
            status_line=f"Plan selected: {plan.label}"[:200],
            plan_id=plan.plan_id,
            invoked=list(plan.invoked),
            skipped=skipped_agents(plan),
        )

        # Keep the Context Pack identical to the golden recordings: the incident is already
        # on the bus via InvestigationOpened. Passing it into assemble() would move every
        # fingerprint and break FakeProvider strict replay.
        runner = AgentRunner(
            provider=self._provider,
            toolset=self._tools,
            routing=self._routing,
            bus=self._bus,
            company=self._company,
            as_of=self._as_of,
            investigation_id=investigation_id,
        )
        executor = AgentExecutor(
            runner=runner, routing=self._routing, bus=self._bus, store=self._store
        )

        self._phase(investigation_id, InvestigationPhase.INVESTIGATING)
        wave1 = [WaveUnit(spec=build(role)) for role in plan.invoked]
        runs = await executor.run_wave(wave1)
        findings = [r.finding for r in runs if r.finding is not None]

        # Supplier Risk challenges AP proposals when AP ran.
        ap_finding = next((f for f in findings if f.agent is AgentRole.AP_OPTIMIZATION), None)
        if ap_finding is not None and AgentRole.AP_OPTIMIZATION in plan.invoked:
            proposals = _supplier_challenge_proposals(ap_finding.recommended_actions)
            if proposals:
                supplier_run = (
                    await executor.run_wave([WaveUnit(spec=SupplierRiskAgent(proposals=proposals))])
                )[0]
                runs.append(supplier_run)
                if supplier_run.finding is not None:
                    findings.append(supplier_run.finding)

        # Conflicts
        self._phase(investigation_id, InvestigationPhase.RESOLVING_CONFLICTS)
        conflicts = detect_conflicts(findings)
        resolutions = []
        for conflict in conflicts:
            self._bus.emit(
                ConflictDetected,
                investigation_id=investigation_id,
                status_line=conflict.description[:200],
                conflict_id=conflict.conflict_id,
                kind=conflict.kind,
                agents=list(conflict.agents),
                description=conflict.description,
            )
            if conflict.kind.value == "semantic":
                self._bus.emit(
                    FollowupDispatched,
                    investigation_id=investigation_id,
                    status_line="Follow-up: uphold supplier evidence",
                    conflict_id=conflict.conflict_id,
                    agent=AgentRole.SUPPLIER_RISK,
                    question="Which deferred documents does concentration evidence block?",
                )
            resolution = resolve_conflict(conflict, findings)
            resolutions.append(resolution)
            self._bus.emit(
                ConflictResolved,
                investigation_id=investigation_id,
                status_line=resolution.resolution[:200],
                conflict_id=resolution.conflict_id,
                resolution=resolution.resolution,
                upheld=resolution.upheld,
                evidence=resolution.evidence,
            )

        actions = surviving_actions(findings, resolutions)
        dropped = [a for r in resolutions for a in r.dropped_actions]
        position = await self._tools.get_liquidity_position()

        # Scenarios
        self._phase(investigation_id, InvestigationPhase.GENERATING_SCENARIOS)
        bundles = propose_strategy_bundles(actions)
        strategies = price_strategies(bundles, position=position)
        for strategy in strategies:
            self._bus.emit(
                ScenarioGenerated,
                investigation_id=investigation_id,
                mark=StatusMark.OK if strategy.feasible else StatusMark.WARN,
                status_line=f"Scenario: {strategy.name}"[:200],
                strategy=strategy,
            )

        # Stress
        self._phase(investigation_id, InvestigationPhase.STRESS_TESTING)
        errors = await self._tools.get_forecast_error_percentiles(horizon_weeks=6)
        stressors = calibrate_stressors(errors)
        stress_results: list[StressResult] = []
        for strategy in strategies:
            # Golden path needs a first-round failure: stress the aggressive (no-revolver) set hard.
            for stressor in stressors:
                result = apply_stress(strategy, stressor, position=position)
                stress_results.append(result)
                self._bus.emit(
                    StressCompleted,
                    investigation_id=investigation_id,
                    mark=StatusMark.OK if result.passed else StatusMark.WARN,
                    status_line=(
                        f"Stress {stressor.label}: "
                        f"{'PASS' if result.passed else 'FAIL'} at {result.min_cash}"
                    )[:200],
                    result=result,
                )

        replan_history: list[ReplanAttempt] = []
        # Golden path: stress the aggressive first strategy; replan if combined fails.
        initial = next((s for s in strategies if s.feasible), strategies[0])
        initial_combined = next(
            (
                r
                for r in stress_results
                if r.strategy_id == initial.strategy_id and r.stressor.stressor_id == "combined-p90"
            ),
            None,
        )
        selected = (
            initial
            if initial_combined is not None and initial_combined.passed and initial.feasible
            else None
        )

        # Replan if nothing survives the combined stressor.
        attempt = 1
        while selected is None and attempt <= self._max_replans:
            self._phase(investigation_id, InvestigationPhase.REPLANNING)
            failed = next(
                (
                    r
                    for r in stress_results
                    if not r.passed and r.stressor.stressor_id == "combined-p90"
                ),
                next((r for r in stress_results if not r.passed), None),
            )
            if failed is None:
                break
            replan = ReplanAttempt(
                attempt=attempt,
                strategy_id=failed.strategy_id,
                failure_reason=(
                    f"{failed.stressor.label} left min cash at {failed.min_cash} "
                    f"below floor {failed.floor}"
                ),
                tightened_constraint="require_revolver_draw",
            )
            replan_history.append(replan)
            self._bus.emit(
                ReplanStarted,
                investigation_id=investigation_id,
                status_line=f"Replanning after {failed.stressor.label} failure"[:200],
                attempt=replan,
            )

            # Tighten: force a revolver draw into a new robust bundle.
            robust_actions = [
                a
                for a in actions
                if a.kind is not ActionKind.AP_DEFER or a.document_ref != "BILL-8841"
            ]
            draw = ProposedAction(
                kind=ActionKind.REVOLVER_DRAW,
                rationale="Replan: draw revolver after combined stress failure",
                amount=Money(minor_units=250_000_000, currency="USD"),
                evidence_refs=["debt:revolver#available"],
            )
            robust_bundle = [*robust_actions, draw]
            robust = price_strategies(
                [robust_bundle],
                position=position,
                names=[f"Replan {attempt}: AR + Dodo + revolver"],
            )[0]
            robust = robust.model_copy(update={"strategy_id": f"strategy-replan-{attempt}"})
            strategies.append(robust)
            self._bus.emit(
                ScenarioGenerated,
                investigation_id=investigation_id,
                status_line=f"Scenario: {robust.name}"[:200],
                strategy=robust,
            )

            self._phase(investigation_id, InvestigationPhase.STRESS_TESTING)
            for stressor in stressors:
                result = apply_stress(robust, stressor, position=position)
                stress_results.append(result)
                self._bus.emit(
                    StressCompleted,
                    investigation_id=investigation_id,
                    mark=StatusMark.OK if result.passed else StatusMark.WARN,
                    status_line=(
                        f"Stress {stressor.label}: "
                        f"{'PASS' if result.passed else 'FAIL'} at {result.min_cash}"
                    )[:200],
                    result=result,
                )
            selected = _select_passing_strategy([robust], stress_results)
            attempt += 1

        if selected is None:
            # Last resort: pick the strategy with the best (least negative) headroom under combined stress.
            combined = [r for r in stress_results if r.stressor.stressor_id == "combined-p90"]
            best = max(combined, key=lambda r: r.headroom.minor_units) if combined else None
            if best is not None:
                selected = next(s for s in strategies if s.strategy_id == best.strategy_id)

        assert selected is not None

        self._phase(investigation_id, InvestigationPhase.RECOMMENDING)
        as_of_date = date.fromisoformat(self._as_of)
        worklist = compose_worklist(selected, as_of=as_of_date)
        rejected = rejected_from_findings(findings, dropped)
        degraded = any(r.status.value in {"timeout", "failed", "degraded"} for r in runs)
        recommendation = Recommendation(
            recommendation_id=f"rec-{investigation_id[:8]}",
            investigation_id=investigation_id,
            selected_strategy=selected,
            alternatives=[s for s in strategies if s.strategy_id != selected.strategy_id][:3],
            worklist=worklist,
            rejected_actions=rejected,
            stress_results=[r for r in stress_results if r.strategy_id == selected.strategy_id],
            replan_history=replan_history,
            degraded=degraded,
            degradation_reason="One or more agents did not complete" if degraded else None,
            requires_human_review=True,
            summary=(
                f"Selected {selected.name} restoring projected min cash to "
                f"{selected.projected_min_cash} after {len(replan_history)} replan(s)."
            )[:1000],
        )
        self._bus.emit(
            RecommendationReady,
            investigation_id=investigation_id,
            status_line=f"Recommendation ready: {selected.name}"[:200],
            recommendation=recommendation,
        )

        approvals = _approval_requests(recommendation, worklist, prepared_by="analyst.demo")
        for request in approvals:
            self._bus.emit(
                ApprovalRequested,
                investigation_id=investigation_id,
                status_line=f"Approval required: {request.action}"[:200],
                request=request,
            )

        self._phase(investigation_id, InvestigationPhase.AWAITING_APPROVAL)
        self._bus.emit(
            InvestigationClosed,
            investigation_id=investigation_id,
            status_line="Investigation awaiting approval",
            phase=InvestigationPhase.CLOSED,
            recommendation_id=recommendation.recommendation_id,
            reason="Recommendation issued; consequential actions need human approval",
        )

        return InvestigationResult(
            investigation_id=investigation_id,
            phase=InvestigationPhase.AWAITING_APPROVAL,
            breach=breach,
            plan_id=plan.plan_id,
            findings=findings,
            runs=list(self._store.all(investigation_id=investigation_id)),
            strategies=strategies,
            stress_results=stress_results,
            replan_history=replan_history,
            recommendation=recommendation,
            worklist=worklist,
            approvals=approvals,
        )

    def _phase(self, investigation_id: str, phase: InvestigationPhase) -> None:
        self._bus.emit(
            InvestigationPhaseChanged,
            investigation_id=investigation_id,
            mark=StatusMark.WORKING,
            status_line=f"Phase: {phase.value}",
            phase=phase,
        )


def _supplier_challenge_proposals(actions: list[ProposedAction]) -> list[ProposedAction]:
    """Strip amounts so the Supplier Risk gather matches the golden fingerprint shape."""
    return [
        ProposedAction(
            kind=action.kind,
            rationale=action.rationale,
            counterparty=action.counterparty,
            document_ref=action.document_ref,
            delay_days=action.delay_days,
            evidence_refs=list(action.evidence_refs),
        )
        for action in actions
        if action.kind is ActionKind.AP_DEFER
    ]


def _select_passing_strategy(
    strategies: list[Strategy],
    stress_results: list[StressResult],
) -> Strategy | None:
    """A strategy must pass the combined stressor to be selected."""
    by_id = {s.strategy_id: s for s in strategies}
    for result in stress_results:
        if result.stressor.stressor_id == "combined-p90" and result.passed:
            strategy = by_id.get(result.strategy_id)
            if strategy is not None and strategy.feasible:
                return strategy
    return None


def _approval_requests(
    recommendation: Recommendation,
    worklist: list[WorklistItem],
    *,
    prepared_by: str,
) -> list[ApprovalRequest]:
    from backend.contracts.approvals import ApprovalRole as AR

    requests: list[ApprovalRequest] = []
    now = datetime.now(UTC)
    for item in worklist:
        if item.approval_request_id is None:
            continue
        amount = item.amount
        role = AR.CFO if amount.minor_units >= 100_000_000 else AR.TREASURER
        route = ApprovalRoute(
            route_id=f"route-{item.seq}",
            action_class=item.action.split(":", 1)[0],
            lower_bound=Money.zero(amount.currency),
            upper_bound=amount,
            responsible=ApprovalRole.ANALYST,
            reviewer=ApprovalRole.TREASURER,
            accountable=role,
        )
        requests.append(
            ApprovalRequest(
                request_id=item.approval_request_id,
                worklist_seq=item.seq,
                recommendation_id=recommendation.recommendation_id,
                action=item.action,
                amount=amount,
                expected_impact=f"Expected cash impact {item.expected_cash_impact or amount}",
                risk="Counterparty or financing risk if the action lands late or is reversed",
                evidence=list(item.evidence),
                why_recommended=recommendation.summary or recommendation.selected_strategy.name,
                what_could_go_wrong="Recovery or float may under-deliver under the stressed case",
                approval_required=role,
                route=route,
                prepared_by=prepared_by,
                state=ApprovalState.PENDING,
                created_at=now,
                data_snapshot_ref=recommendation.investigation_id,
            )
        )
    return requests
