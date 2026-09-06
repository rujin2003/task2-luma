"""The Monday 13-week forecast cycle.

Ten steps from WORKFLOW.md §4. A policy breach at the end opens the war room; otherwise
the cycle publishes and stays quiet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from backend.agents.provider import LLMProvider
from backend.agents.replay import ReplayProvider
from backend.agents.routing import ModelRouting, load_routing
from backend.agents.runner import AgentRunner
from backend.agents.specialists import build
from backend.contracts.agent import AgentRole
from backend.contracts.constraints import ConstraintViolation
from backend.contracts.events import CycleStepCompleted, InvestigationPhase, StatusMark
from backend.orchestrator.bus import EventBus
from backend.orchestrator.investigation import InvestigationResult, InvestigationRunner
from backend.orchestrator.policy_check import check_liquidity_policy
from backend.tools.toolset import Toolset

CYCLE_STEPS: tuple[tuple[int, str], ...] = (
    (1, "Refresh actuals"),
    (2, "Classify to forecast categories"),
    (3, "Bank reconciliation"),
    (4, "Variance bridge"),
    (5, "Driver refresh and reforecast"),
    (6, "Accuracy roll-forward"),
    (7, "Exception surfacing"),
    (8, "Review and challenge"),
    (9, "Publish forecast version"),
    (10, "Policy check"),
)


@dataclass(frozen=True, slots=True)
class CycleResult:
    forecast_version_id: str
    published: bool
    breach: ConstraintViolation | None
    investigation: InvestigationResult | None
    steps_completed: tuple[int, ...]


async def run_monday_cycle(
    *,
    tools: Toolset,
    bus: EventBus,
    provider: LLMProvider | None = None,
    routing: ModelRouting | None = None,
    company: str = "NovaTech Industries",
    as_of: str = "2026-03-02",
    auto_investigate: bool = True,
    incident_kind: str | None = None,
) -> CycleResult:
    """Run the ten-step cycle; open an investigation when policy is breached."""
    provider = provider or ReplayProvider(strict=True)
    routing = routing or load_routing()
    forecast = await tools.get_forecast_summary()
    version_id = forecast.version_id
    completed: list[int] = []

    for step, name in CYCLE_STEPS:
        if step == 4:
            # Variance agent explains material deltas.
            runner = AgentRunner(
                provider=provider,
                toolset=tools,
                routing=routing,
                bus=bus,
                company=company,
                as_of=as_of,
            )
            await runner.run(build(AgentRole.VARIANCE), run_id=f"cycle-variance-{as_of}")
        elif step == 5:
            runner = AgentRunner(
                provider=provider,
                toolset=tools,
                routing=routing,
                bus=bus,
                company=company,
                as_of=as_of,
            )
            await runner.run(build(AgentRole.FORECAST), run_id=f"cycle-forecast-{as_of}")
        elif step == 9:
            # Publish is a lock in the product; fixtures already carry a version id.
            pass

        bus.emit(
            CycleStepCompleted,
            mark=StatusMark.OK,
            status_line=f"Cycle step {step}: {name}"[:200],
            step=step,
            name=name,
            forecast_version_id=version_id if step >= 9 else None,
        )
        completed.append(step)

    position = await tools.get_liquidity_position()
    covenants = await tools.get_covenant_status()
    policy = await tools.get_policy_constraints()
    breach = check_liquidity_policy(position, covenants, policy)

    investigation: InvestigationResult | None = None
    if breach is not None and auto_investigate:
        shortfall = ""
        if breach.observed_money is not None:
            shortfall = f" ({breach.observed_money} vs {breach.threshold_display})"
        trigger = f"Policy breach: {breach.constraint_id}{shortfall}"
        investigation = await InvestigationRunner(
            tools=tools,
            bus=bus,
            provider=provider,
            routing=routing,
            company=company,
            as_of=as_of,
        ).run(
            breach=breach,
            trigger=trigger,
            incident_kind=incident_kind,
        )

    return CycleResult(
        forecast_version_id=version_id,
        published=True,
        breach=breach,
        investigation=investigation,
        steps_completed=tuple(completed),
    )


def cycle_as_of_today() -> str:
    return date.today().isoformat()


# Re-export for callers that want the phase enum without another import.
__all__ = [
    "CYCLE_STEPS",
    "CycleResult",
    "InvestigationPhase",
    "cycle_as_of_today",
    "run_monday_cycle",
]
