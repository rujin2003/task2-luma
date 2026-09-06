"""The Commander: decides the shape of an investigation and dispatches the wave.

It does three things and deliberately not a fourth.

* **It selects a plan from an enumerated catalogue.** The menu is filtered by the tenant
  capability manifest before the model sees it, so an unrunnable investigation is not
  merely discouraged, it is unofferable.
* **It justifies every omission.** `PlanSelected` carries a written reason per specialist
  that was not called. "Why wasn't Dodo asked?" has an answer on the record, and the
  answer distinguishes "this tenant has no Dodo connection" from "subscription receipts
  are on plan" -- different facts, and the treasurer is owed the true one.
* **It dispatches, in dependency order.** Supplier Risk is adversarial by design: it
  exists to reject the AP agent's actual proposals, so it cannot run in the same wave as
  the agent it is challenging. Everything else runs in parallel.

What it does not do is decide anything numeric. The plan is a routing decision.

If the model is unavailable, rate-limited, or names a plan that is not on the menu, the
Commander falls back to the widest runnable plan and says so on the stream. An
investigation that ran the default plan is a worse investigation; one that did not run at
all because a free tier throttled us is not an investigation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from backend.agents.choice import choose
from backend.agents.context import Incident
from backend.agents.prompts import load_prompt
from backend.agents.provider import LLMProvider
from backend.agents.routing import ModelRouting
from backend.agents.runner import AgentRunner
from backend.agents.specialists import build
from backend.agents.specialists.base import fetch
from backend.agents.specialists.supplier_risk import SupplierRiskAgent
from backend.contracts.agent import (
    ActionKind,
    AgentFinding,
    AgentRole,
    AgentRun,
    AgentStatus,
    ProposedAction,
)
from backend.contracts.constraints import ConstraintKind
from backend.contracts.events import (
    PlanSelected,
    SkippedAgent,
    StatusMark,
    SystemDegraded,
)
from backend.orchestrator.bus import EventBus
from backend.orchestrator.executor import AgentExecutor, WaveUnit
from backend.orchestrator.plans import (
    PLANS_BY_ID,
    InvestigationPlan,
    PlanChoice,
    fallback_plan,
    plans_for,
)
from backend.orchestrator.runs import RunStore
from backend.tools.registry import ScopedToolset
from backend.tools.results import CapabilityManifest, CovenantStatus, LiquidityPosition
from backend.tools.toolset import ToolError, Toolset

COMMANDER_TASK = (
    "Choose the investigation plan whose shape matches where this breach actually comes "
    "from, and say which evidence made it the right one."
)


@dataclass(slots=True)
class Dispatch:
    """One investigation's plan and everything the wave produced under it."""

    plan: InvestigationPlan
    rationale: str
    skipped: list[SkippedAgent]
    runs: list[AgentRun] = field(default_factory=list)
    model_selected: bool = True
    fallback_reason: str = ""

    @property
    def findings(self) -> list[AgentFinding]:
        """Findings that survived validation, in the order the plan invoked them."""
        return [run.finding for run in self.runs if run.finding is not None]

    @property
    def degraded(self) -> bool:
        return any(
            run.status not in {AgentStatus.COMPLETE, AgentStatus.REFUSED} for run in self.runs
        )

    def degradation_reasons(self) -> list[str]:
        return [
            f"{run.agent.value}: {run.failure_reason or run.status.value}"
            for run in self.runs
            if run.status not in {AgentStatus.COMPLETE, AgentStatus.REFUSED}
        ]


class Commander:
    """Plans one investigation and runs it."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        toolset: Toolset,
        routing: ModelRouting,
        bus: EventBus,
        company: str,
        as_of: str,
        investigation_id: str,
        incident: Incident | None = None,
        store: RunStore | None = None,
    ) -> None:
        self._provider = provider
        self._toolset = toolset
        self._routing = routing
        self._bus = bus
        self._company = company
        self._as_of = as_of
        self._incident = incident
        self._store = store
        self.investigation_id = investigation_id

    # --- planning ----------------------------------------------------------------------

    async def plan(self, breach_kinds: tuple[ConstraintKind, ...] = ()) -> Dispatch:
        """Pick a plan from the runnable menu and record why the rest were not."""
        scoped = ScopedToolset(self._toolset, AgentRole.COMMANDER)
        manifest, brief = await self._survey(scoped)
        menu = plans_for(manifest, breach_kinds)

        selection = await choose(
            PlanChoice,
            role=AgentRole.COMMANDER,
            system_prompt=load_prompt(AgentRole.COMMANDER),
            task=COMMANDER_TASK,
            brief_lines=brief + [plan.menu_line() for plan in menu],
            provider=self._provider,
            routing=self._routing,
            bus=self._bus,
            company=self._company,
            as_of=self._as_of,
            scoped=scoped,
            incident=self._incident,
            investigation_id=self.investigation_id,
            run_id=f"{self.investigation_id}-commander",
        )

        offered = {plan.plan_id for plan in menu}
        chosen: InvestigationPlan
        fallback_reason = ""
        if selection.value is None:
            fallback_reason = selection.failure_reason or "the model returned no plan"
            chosen = fallback_plan(manifest)
        elif selection.value.plan_id not in offered:
            # A plan id that is not on the menu is either a hallucination or a plan this
            # tenant cannot run. Both are the same failure from here.
            fallback_reason = (
                f"the model chose {selection.value.plan_id!r}, which was not on the menu "
                f"({', '.join(sorted(offered))})"
            )
            chosen = fallback_plan(manifest)
        else:
            chosen = PLANS_BY_ID[selection.value.plan_id]

        if fallback_reason:
            self._bus.emit(
                SystemDegraded,
                investigation_id=self.investigation_id,
                mark=StatusMark.WARN,
                status_line="Commander fell back to the default plan",
                component="commander",
                reason=fallback_reason[:400],
            )

        rationale = (
            selection.value.rationale
            if selection.value is not None and not fallback_reason
            else f"Default plan: {fallback_reason}"
        )
        skipped = chosen.skips(manifest)
        self._bus.emit(
            PlanSelected,
            investigation_id=self.investigation_id,
            status_line=f"Plan: {chosen.label} ({len(chosen.invokes)} agents)",
            plan_id=chosen.plan_id,
            invoked=list(chosen.invokes),
            skipped=skipped,
        )
        return Dispatch(
            plan=chosen,
            rationale=rationale,
            skipped=skipped,
            model_selected=not fallback_reason,
            fallback_reason=fallback_reason,
        )

    async def _survey(self, scoped: ScopedToolset) -> tuple[CapabilityManifest, list[str]]:
        """What the Commander is allowed to look at before it plans: shape, not detail."""
        manifest = await fetch(scoped, "get_capability_manifest", CapabilityManifest)
        position = await fetch(scoped, "get_liquidity_position", LiquidityPosition)

        lines = [
            f"available sources: {', '.join(s.value for s in manifest.available_sources)}",
        ]
        if manifest.missing_sources:
            lines.append("missing sources: " + ", ".join(s.value for s in manifest.missing_sources))
        lines.append(
            f"position: cash {position.cash_today}, 13-week minimum {position.min_cash} at "
            f"W{position.min_cash_week} against a {position.floor} floor, "
            f"{position.revolver_available} undrawn"
        )
        try:
            covenants = await fetch(scoped, "get_covenant_status", CovenantStatus)
        except ToolError as exc:
            # A covenant we cannot read is named as unread. The Commander plans on what it
            # can see, and the gap is on the record rather than absorbed into the brief.
            lines.append(f"covenant status unavailable ({exc})")
        else:
            for row in covenants.covenants:
                lines.append(
                    f"covenant {row.covenant_id}: {row.observed_ratio} vs "
                    f"{row.threshold_ratio}, {'BREACHED' if row.breached else 'inside'}"
                )
        return manifest, lines

    # --- dispatch ----------------------------------------------------------------------

    async def investigate(self, dispatch: Dispatch) -> Dispatch:
        """Run the plan: the parallel wave, then the adversarial pass over its output."""
        runner = AgentRunner(
            provider=self._provider,
            toolset=self._toolset,
            routing=self._routing,
            bus=self._bus,
            company=self._company,
            as_of=self._as_of,
            investigation_id=self.investigation_id,
            incident=self._incident,
        )
        executor = AgentExecutor(
            runner=runner,
            routing=self._routing,
            bus=self._bus,
            store=self._store,
        )

        # Wave one: everyone whose work depends only on the ledger.
        first = [role for role in dispatch.plan.invokes if role is not AgentRole.SUPPLIER_RISK]
        runs = await executor.run_wave(
            [WaveUnit(spec=build(role), run_id=self._run_id(role)) for role in first]
        )
        dispatch.runs.extend(runs)

        # Wave two: Supplier Risk, holding the AP agent's actual proposals. Handing it a
        # summary instead would make "reject this specific proposal with evidence" into a
        # sentiment; it has to be given the rows it is being asked to rule on.
        if AgentRole.SUPPLIER_RISK in dispatch.plan.invokes:
            proposals = _deferrals(runs)
            challenge = await executor.run_wave(
                [
                    WaveUnit(
                        spec=SupplierRiskAgent(proposals=proposals),
                        prior_findings=[run.finding for run in runs if run.finding is not None],
                        run_id=self._run_id(AgentRole.SUPPLIER_RISK),
                    )
                ]
            )
            dispatch.runs.extend(challenge)

        return dispatch

    def _run_id(self, role: AgentRole) -> str:
        return f"{self.investigation_id}-{role.value}-{uuid.uuid4().hex[:6]}"


def _deferrals(runs: list[AgentRun]) -> list[ProposedAction]:
    """The AP deferrals this wave actually proposed, for Supplier Risk to rule on."""
    return [
        action
        for run in runs
        if run.agent is AgentRole.AP_OPTIMIZATION and run.finding is not None
        for action in run.finding.recommended_actions
        if action.kind is ActionKind.AP_DEFER
    ]
