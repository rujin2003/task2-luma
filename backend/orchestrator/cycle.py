"""The Monday cycle -- the ten steps of `WORKFLOW.md` section 4, in order.

This is the product. The war room is what happens when step 10 fails; it is not the thing
the analyst opens on a quiet week. A system that only performs during a liquidity crisis is
unused on the fifty other Mondays of the year, so the crisis branch hangs off this loop
rather than the other way round.

Three properties are worth stating plainly, because they are what separate this from a
scripted demo:

* **Only steps 3 and 7 involve a model.** Refreshing, bridging, reforecasting, rolling
  accuracy forward and checking policy are arithmetic, and arithmetic is the engine's.
  The model explains material deltas and nothing else.
* **Materiality gates the explaining.** A bridge that explains a $30K variance signals
  that nobody involved has done this job. Immaterial rows are bucketed and named as such.
* **A material row we could not explain says so.** If the Variance Agent times out, or
  cites a row it never saw, the affected bridge lines carry an `unexplained_reason` rather
  than a blank. An unexplained material delta is information; a blank line is a lie of
  omission that lets a treasurer believe the bridge is complete.

Steps 8 and 9 -- review, challenge, publish -- are the human ones and live in `review.py`.
The cycle prepares them and stops; it does not sign its own work off.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.agents.runner import AgentRunner
from backend.agents.specialists import build
from backend.contracts.agent import AgentFinding, AgentRole, AgentRun, AgentStatus
from backend.contracts.events import CycleStepCompleted, StatusMark, SystemDegraded
from backend.contracts.money import Money
from backend.contracts.provenance import Evidence
from backend.orchestrator.bus import EventBus
from backend.orchestrator.policy import PolicyCheck, check_policy
from backend.orchestrator.review import ReviewLedger
from backend.orchestrator.runs import RunStore
from backend.tools.results import (
    AssumptionRow,
    CovenantStatus,
    DriverAssumptions,
    ForecastSummary,
    LiquidityPosition,
    PolicyConstraints,
    VarianceBridge,
    VarianceRow,
)
from backend.tools.toolset import ToolError, Toolset

# The ten steps, named once. The UI, the event stream and the tests all read these, so a
# renamed step is a visible diff rather than three strings drifting apart.
CYCLE_STEPS: tuple[str, ...] = (
    "Refresh actuals",
    "Build variance bridge",
    "Explain the variance",
    "Refresh drivers",
    "Reforecast weeks 1-13",
    "Update accuracy",
    "Surface exceptions",
    "Review & challenge",
    "Publish",
    "Policy check",
)

# Steps 8 and 9 are human. The cycle runs 1-7, hands over, and resumes at 10 once the
# Treasurer has published -- so the automated run stops here and says so.
LAST_AUTOMATED_STEP = 7

Severity = Literal["critical", "warning", "info"]


class ExplainedVariance(BaseModel):
    """One bridge row, with the agent's root cause attached if there is one."""

    model_config = ConfigDict(frozen=True)

    category: str
    plan: Money
    actual: Money
    delta: Money
    material: bool
    reference: str
    explanation: str = ""
    explained_by: AgentRole | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    unexplained_reason: str | None = None

    @classmethod
    def immaterial(cls, row: VarianceRow, basis: str) -> ExplainedVariance:
        return cls(
            category=row.category,
            plan=row.plan,
            actual=row.actual,
            delta=row.delta,
            material=False,
            reference=row.reference,
            explanation=basis,
        )


class CycleException(BaseModel):
    """Something that moved materially, or an assumption that went stale. Not everything."""

    model_config = ConfigDict(frozen=True)

    exception_id: Annotated[str, Field(min_length=1)]
    severity: Severity
    headline: Annotated[str, Field(min_length=1, max_length=200)]
    detail: Annotated[str, Field(max_length=600)] = ""
    week_index: Annotated[int, Field(ge=1, le=13)] | None = None
    reference: str | None = None


class CycleResult(BaseModel):
    """Everything one Monday produced, including what it could not do."""

    model_config = ConfigDict(frozen=True)

    cycle_id: str
    as_of: str
    company: str
    forecast_version_id: str
    steps_completed: list[str] = Field(default_factory=list)

    position: LiquidityPosition
    bridge_week_ending: str
    total_delta: Money
    bridge: list[ExplainedVariance] = Field(default_factory=list)
    immaterial_basis: str = ""
    drivers: list[AssumptionRow] = Field(default_factory=list)
    forecast: ForecastSummary
    exceptions: list[CycleException] = Field(default_factory=list)
    policy: PolicyCheck | None = None

    degraded: bool = False
    degradation_reasons: list[str] = Field(default_factory=list)

    @property
    def escalate(self) -> bool:
        return self.policy is not None and self.policy.escalate

    @property
    def unexplained(self) -> list[ExplainedVariance]:
        return [row for row in self.bridge if row.material and row.unexplained_reason]

    def critical_exceptions(self) -> list[CycleException]:
        return [item for item in self.exceptions if item.severity == "critical"]


class WeeklyCycle:
    """Runs steps 1-7, then step 10 once the human steps are done.

    The split is deliberate: `run()` returns at the review gate with everything an analyst
    needs to review, and `policy_check()` is called after the Treasurer publishes. A cycle
    that checked policy against an unpublished draft would escalate on a number nobody has
    signed, and a war room opened on a draft is a war room nobody trusts.
    """

    def __init__(
        self,
        *,
        toolset: Toolset,
        bus: EventBus,
        company: str,
        as_of: str,
        runner: AgentRunner | None = None,
        store: RunStore | None = None,
        cycle_id: str | None = None,
    ) -> None:
        self._toolset = toolset
        self._bus = bus
        self._company = company
        self._as_of = as_of
        self._runner = runner
        self._store = store
        self.cycle_id = cycle_id or str(uuid.uuid4())
        self.variance_run: AgentRun | None = None

    async def run(self) -> CycleResult:
        """Steps 1-7. Stops at the review gate, which is a human's to open."""
        degradations: list[str] = []
        completed: list[str] = []

        def done(step: int, *, version_id: str | None = None) -> None:
            completed.append(CYCLE_STEPS[step - 1])
            self._bus.emit(
                CycleStepCompleted,
                status_line=f"Step {step}: {CYCLE_STEPS[step - 1]}",
                step=step,
                name=CYCLE_STEPS[step - 1],
                forecast_version_id=version_id,
            )

        # 1. Refresh actuals. Where we actually stand, from the engine, before anything
        #    is compared to anything.
        position = await self._toolset.get_liquidity_position()
        done(1)

        # 2. Build the variance bridge. The single most-used artifact of the cycle.
        bridge = await self._toolset.get_variance_bridge()
        done(2)

        # 3. Explain it -- the one model call in the automated half, and only for the
        #    rows that cleared materiality.
        material = [row for row in bridge.rows if row.material]
        finding, explain_failure = await self._explain(material)
        if explain_failure:
            degradations.append(explain_failure)
        explained = self._attach(bridge, finding, explain_failure)
        done(3)

        # 4. Refresh drivers.
        drivers = await self._toolset.list_driver_assumptions()
        done(4)

        # 5. Reforecast. The engine computes it; we read the version it produced.
        forecast = await self._toolset.get_forecast_summary()
        done(5, version_id=forecast.version_id)

        # 6. Roll accuracy forward. Empirical error is what replaces model confidence, so
        #    a cycle that cannot measure it has to say so rather than quietly skip it.
        try:
            await self._toolset.get_forecast_error_percentiles(horizon_weeks=1)
        except ToolError as exc:
            degradations.append(f"accuracy roll-forward unavailable: {exc}")
            self._bus.emit(
                SystemDegraded,
                mark=StatusMark.WARN,
                status_line="accuracy roll-forward unavailable",
                component="accuracy",
                reason=str(exc)[:400],
            )
        done(6)

        # 7. Surface exceptions -- only what moved materially or went stale.
        exceptions = _surface_exceptions(explained, drivers, forecast, position)
        done(7)

        for reason in degradations:
            self._bus.emit(
                SystemDegraded,
                mark=StatusMark.WARN,
                status_line="cycle degraded",
                component="weekly_cycle",
                reason=reason[:400],
            )

        return CycleResult(
            cycle_id=self.cycle_id,
            as_of=self._as_of,
            company=self._company,
            forecast_version_id=forecast.version_id,
            steps_completed=completed,
            position=position,
            bridge_week_ending=str(bridge.week_ending),
            total_delta=bridge.total_delta,
            bridge=explained,
            immaterial_basis=_immaterial_basis(bridge),
            drivers=list(drivers.rows),
            forecast=forecast,
            exceptions=exceptions,
            degraded=bool(degradations),
            degradation_reasons=degradations,
        )

    async def policy_check(self, result: CycleResult, ledger: ReviewLedger) -> PolicyCheck:
        """Step 10, run against the version the Treasurer actually published."""
        if ledger.published is None:
            raise RuntimeError(
                "the policy check runs against a published version; "
                "publish the cycle before checking it"
            )

        policy: PolicyConstraints = await self._toolset.get_policy_constraints()
        covenants: CovenantStatus | None
        try:
            covenants = await self._toolset.get_covenant_status()
        except ToolError:
            # Covenants tested on a date we cannot read are reported as unchecked by
            # `check_policy`, which is the honest outcome -- not a silent pass.
            covenants = None

        check = check_policy(
            as_of=self._as_of,
            policy=policy,
            position=result.position,
            forecast=result.forecast,
            covenants=covenants,
        )
        self._bus.emit(
            CycleStepCompleted,
            mark=StatusMark.FAIL if check.escalate else StatusMark.OK,
            status_line=(
                f"Step 10: {CYCLE_STEPS[9]} -- "
                + (
                    f"{len(check.hard_violations)} hard breach(es), escalating"
                    if check.escalate
                    else "within policy"
                )
            ),
            step=10,
            name=CYCLE_STEPS[9],
            forecast_version_id=ledger.published.version_id,
        )
        return check

    # --- step 3 internals ---------------------------------------------------------------

    async def _explain(self, material: Sequence[VarianceRow]) -> tuple[AgentFinding | None, str]:
        """Run the Variance Agent, or say why the bridge has no explanations."""
        if not material:
            return None, ""
        if self._runner is None:
            return None, "no agent runtime configured for this cycle"

        run = await self._runner.run(build(AgentRole.VARIANCE), run_id=f"{self.cycle_id}-variance")
        self.variance_run = run
        if self._store is not None:
            self._store.add(run)
        if run.status is not AgentStatus.COMPLETE or run.finding is None:
            return (
                None,
                f"variance explanation {run.status.value}: {run.failure_reason or 'no finding'}",
            )
        return run.finding, ""

    def _attach(
        self, bridge: VarianceBridge, finding: AgentFinding | None, failure: str
    ) -> list[ExplainedVariance]:
        """Match the agent's cited rows to the bridge lines they explain.

        Matching is on the reference, not on the model's own claim about which category it
        was talking about -- the reference is the thing the evidence validator already
        resolved against the ledger, and it is the only handle here worth trusting.
        """
        basis = _immaterial_basis(bridge)
        cited = {evidence.reference: evidence for evidence in (finding.evidence if finding else [])}

        rows: list[ExplainedVariance] = []
        for row in bridge.rows:
            if not row.material:
                rows.append(ExplainedVariance.immaterial(row, basis))
                continue

            evidence = cited.get(row.reference)
            if finding is None or evidence is None:
                rows.append(
                    ExplainedVariance(
                        category=row.category,
                        plan=row.plan,
                        actual=row.actual,
                        delta=row.delta,
                        material=True,
                        reference=row.reference,
                        unexplained_reason=(
                            failure
                            or "the Variance Agent did not cite a source row for this category"
                        ),
                    )
                )
                continue

            rows.append(
                ExplainedVariance(
                    category=row.category,
                    plan=row.plan,
                    actual=row.actual,
                    delta=row.delta,
                    material=True,
                    reference=row.reference,
                    # The excerpt is the ledger row's own text -- the validator overwrote
                    # whatever the model wrote there before this finding was surfaced.
                    explanation=finding.detail or finding.headline,
                    explained_by=finding.agent,
                    evidence=[evidence],
                )
            )
        return rows


def _immaterial_basis(bridge: VarianceBridge) -> str:
    """What the immaterial bucket means, stated on the bridge rather than assumed.

    A treasurer who cannot see why a row went unexplained will not trust the ones that
    were, so the count and the total are shown even though neither is actionable.
    """
    immaterial = [row for row in bridge.rows if not row.material]
    if not immaterial:
        return "no immaterial rows this week"
    total = immaterial[0].delta
    for row in immaterial[1:]:
        total = total + row.delta
    return f"{len(immaterial)} immaterial row(s), {total} net, below the materiality threshold"


def _surface_exceptions(
    bridge: Sequence[ExplainedVariance],
    drivers: DriverAssumptions,
    forecast: ForecastSummary,
    position: LiquidityPosition,
) -> list[CycleException]:
    """Step 7. Only what moved materially, or where an assumption went stale.

    Ordered by severity so the queue reads top-down the way an analyst triages: the weeks
    that breach first, then the assumptions that are least trustworthy, then the deltas
    nobody could account for.
    """
    exceptions: list[CycleException] = []

    for week in forecast.weeks:
        if not week.breaches_floor:
            continue
        exceptions.append(
            CycleException(
                exception_id=f"breach-w{week.week_index}",
                severity="critical",
                headline=(
                    f"W{week.week_index} closing cash {week.closing_cash} breaches the "
                    f"{position.floor} floor"
                ),
                detail=f"Week ending {week.week_ending} on version {forecast.version_id}.",
                week_index=week.week_index,
                reference=forecast.references[0] if forecast.references else None,
            )
        )

    for row in bridge:
        if row.material and row.unexplained_reason:
            exceptions.append(
                CycleException(
                    exception_id=f"unexplained-{row.reference}",
                    severity="warning",
                    headline=f"{row.category}: {row.delta} unexplained",
                    detail=row.unexplained_reason,
                    reference=row.reference,
                )
            )

    for driver in drivers.rows:
        if not driver.stale:
            continue
        exceptions.append(
            CycleException(
                exception_id=f"stale-{driver.reference}",
                severity="warning",
                headline=f"{driver.category}: {driver.driver} is {driver.days_since_refresh}d old",
                detail=(
                    f"Last refreshed {driver.last_refreshed}, currently {driver.value_display}. "
                    "A stale driver repeats its error every week until it is refreshed."
                ),
                reference=driver.reference,
            )
        )

    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(exceptions, key=lambda item: (order[item.severity], item.exception_id))
