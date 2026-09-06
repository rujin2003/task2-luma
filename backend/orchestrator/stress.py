"""Stress testing: the math is deterministic, the calibration is measured, the choice is not.

Three separate claims, and keeping them separate is the whole design.

**The magnitude is measured, never invented.** A stressor is built from this company's own
forecast error at the horizon that matters -- "AR at the 90th percentile of our observed
26-week error at W6" -- because `WORKFLOW.md` section 6 is right that a treasurer will
distrust an arbitrary minus-ten-percent on sight and should. A stressor that cannot be
calibrated from a measured percentile is not offered; it is named as uncalibrated, in the
same way the policy check names constraints it could not observe.

**The arithmetic is code.** Applying a shift to a bundle's expected inflows and comparing
the result to the floor is subtraction. Nothing about that improves by asking a model.

**The selection is agentic.** Which adverse conditions are worth testing this plan against
is a judgement about what the plan depends on, and that is a reasonable thing to ask for.
When the model is unavailable the fallback is every calibrated stressor plus the combined
case, which is the conservative choice: an untested plan is worse than an over-tested one.

Category shifts apply to the lever classes they actually govern. An AR stressor does not
touch a revolver draw, because a draw does not become smaller when customers pay late --
and a stress model that pretends otherwise flatters every financing bundle.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from backend.agents.choice import choose
from backend.agents.context import Incident
from backend.agents.prompts import load_prompt
from backend.agents.provider import LLMProvider
from backend.agents.routing import ModelRouting
from backend.contracts.agent import ActionKind, AgentRole
from backend.contracts.events import StatusMark, StressCompleted, SystemDegraded
from backend.contracts.money import Money, money_sum
from backend.contracts.strategy import Strategy, Stressor, StressResult
from backend.orchestrator.bus import EventBus
from backend.tools.registry import ScopedToolset
from backend.tools.results import ForecastErrorPercentiles, LiquidityPosition
from backend.tools.toolset import ToolError, Toolset

STRESS_TASK = (
    "Choose the calibrated stressors this bundle should be tested against, and say which "
    "part of the bundle each one is aimed at."
)

COMBINED_ID = "combined"

# Which lever classes each error category governs. A stressor that moved every lever would
# be a haircut on the whole plan, not a test of anything in particular.
CATEGORY_KINDS: dict[str, frozenset[ActionKind]] = {
    "ar_collections": frozenset(
        {
            ActionKind.COLLECTION_CALL,
            ActionKind.EARLY_PAY_DISCOUNT,
            ActionKind.DISPUTE_RESOLUTION,
        }
    ),
    "dodo_receipts": frozenset({ActionKind.DODO_RETRY, ActionKind.DODO_DUNNING}),
}

# What each calibrated category is called on a screen, and what the shock represents.
CATEGORY_LABELS: dict[str, str] = {
    "ar_collections": "AR recovery shortfall",
    "dodo_receipts": "Subscription payment-failure spike",
}

# Stressors the spec names that this tenant's measurements cannot support. Named rather
# than approximated: a stress test with an invented magnitude is worse than no stress test,
# because it is the one a treasurer will believe.
UNCALIBRATED: dict[str, str] = {
    "fx_shock": (
        "the position is single-currency, so there is no measured FX error to calibrate "
        "against and no exposure for a shock to move"
    ),
    "financing_cost_increase": (
        "the all-in draw rate lives behind get_debt_capacity, which is not behind the "
        "async tool layer yet; a rate is never invented"
    ),
    "supplier_acceleration": (
        "no measured error series exists for supplier payment timing, so the magnitude of "
        "an acceleration would have to be guessed"
    ),
    "customer_default": (
        "sizing a single-name default needs the AR ledger, which is outside the stress "
        "role's tool allowlist by design"
    ),
    "unexpected_tax": (
        "statutory amounts are contractual rather than forecast, so they carry no error "
        "distribution to take a percentile from"
    ),
}


class StressorSelection(BaseModel):
    """The stress role's output schema: ids from the menu, and why those."""

    model_config = ConfigDict(frozen=True)

    stressor_ids: list[str] = Field(default_factory=list)
    rationale: Annotated[str, Field(min_length=1, max_length=400)] = "n/a"


class StressReport(BaseModel):
    """One bundle, every stressor it was tested against, and what could not be tested."""

    model_config = ConfigDict(frozen=True)

    strategy_id: str
    results: list[StressResult] = Field(default_factory=list)
    rationale: str = ""
    model_selected: bool = True
    fallback_reason: str = ""
    uncalibrated: dict[str, str] = Field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)

    def worst(self) -> StressResult | None:
        if not self.results:
            return None
        return min(self.results, key=lambda r: r.headroom.minor_units)

    def failure_reason(self) -> str:
        """The specific sentence that is fed back into the replan."""
        worst = self.worst()
        if worst is None or worst.passed:
            return ""
        return (
            f"{worst.stressor.label} leaves {worst.min_cash} at W{worst.min_cash_week}, "
            f"{abs(worst.headroom)} below the {worst.floor} floor "
            f"({worst.stressor.calibration})"
        )


def calibrate(percentiles: ForecastErrorPercentiles, *, horizon_weeks: int) -> list[Stressor]:
    """Build the menu from measured error at the horizon that actually matters.

    The trough week is the horizon, not week 13 and not week 1: a plan is judged on
    whether it survives the week it was written for.
    """
    rows = [row for row in percentiles.percentiles if row.category in CATEGORY_KINDS]
    if not rows:
        return []

    chosen: dict[str, Stressor] = {}
    for category in sorted({row.category for row in rows}):
        candidates = [row for row in rows if row.category == category]
        # Nearest measured horizon at or beyond the trough; a shorter horizon would
        # understate the error a longer wait actually carries.
        at_or_beyond = [row for row in candidates if row.horizon_weeks >= horizon_weeks]
        row = min(
            at_or_beyond or candidates,
            key=lambda r: abs(r.horizon_weeks - horizon_weeks),
        )
        chosen[category] = Stressor(
            stressor_id=category,
            label=CATEGORY_LABELS.get(category, category),
            category=category,
            shift_pct=-row.p90_pct,
            calibration=(
                f"90th percentile of this company's own W{row.horizon_weeks} error over "
                f"{row.sample_size} weeks: {row.p90_pct}%"
            )[:280],
        )

    stressors = list(chosen.values())
    if len(stressors) > 1:
        # Adverse conditions correlate. Testing them one at a time is how a plan passes
        # every test and then fails the week.
        stressors.append(
            Stressor(
                stressor_id=COMBINED_ID,
                label="Combined adverse case",
                category=COMBINED_ID,
                shift_pct=min(s.shift_pct for s in stressors),
                calibration=(
                    "every calibrated stressor applied together: "
                    + "; ".join(f"{s.label} {s.shift_pct}%" for s in stressors)
                )[:280],
            )
        )
    return stressors


def apply(
    strategy: Strategy,
    stressor: Stressor,
    *,
    position: LiquidityPosition,
    menu: list[Stressor],
) -> StressResult:
    """Deterministic. Shift the lever classes the stressor governs, then compare."""
    currency = position.floor.currency
    shifts = _shifts(stressor, menu)

    stressed: list[Money] = []
    for action in strategy.actions:
        if action.amount is None:
            continue
        shift = next(
            (pct for category, pct in shifts.items() if action.kind in CATEGORY_KINDS[category]),
            None,
        )
        if shift is None:
            # A revolver draw does not shrink because customers paid late. Leaving it
            # untouched is what stops a financing bundle flattering itself under stress.
            stressed.append(action.amount)
            continue
        stressed.append(action.amount.scale((Decimal(100) + shift) / Decimal(100)))

    min_cash = position.min_cash + money_sum(stressed, currency)
    return StressResult(
        strategy_id=strategy.strategy_id,
        stressor=stressor,
        min_cash=min_cash,
        min_cash_week=position.min_cash_week,
        floor=position.floor,
        passed=min_cash >= position.floor,
        headroom=min_cash - position.floor,
    )


def _shifts(stressor: Stressor, menu: list[Stressor]) -> dict[str, Decimal]:
    if stressor.category != COMBINED_ID:
        return {stressor.category: stressor.shift_pct}
    return {s.category: s.shift_pct for s in menu if s.category != COMBINED_ID}


class StressTester:
    """Calibrates the menu, asks which of it to run, and runs the arithmetic."""

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
    ) -> None:
        self._provider = provider
        self._toolset = toolset
        self._routing = routing
        self._bus = bus
        self._company = company
        self._as_of = as_of
        self._incident = incident
        self.investigation_id = investigation_id

    async def run(self, strategy: Strategy, *, position: LiquidityPosition) -> StressReport:
        scoped = ScopedToolset(self._toolset, AgentRole.STRESS_TEST)
        menu, brief = await self._menu(scoped, position)
        if not menu:
            self._bus.emit(
                SystemDegraded,
                investigation_id=self.investigation_id,
                mark=StatusMark.WARN,
                status_line="no stressor could be calibrated",
                component="stress_test",
                reason="the tenant has no measured forecast error in a stressable category",
            )
            return StressReport(
                strategy_id=strategy.strategy_id,
                rationale="No stressor could be calibrated from measured error.",
                model_selected=False,
                fallback_reason="no calibrated stressor available",
                uncalibrated=UNCALIBRATED,
            )

        selection = await choose(
            StressorSelection,
            role=AgentRole.STRESS_TEST,
            system_prompt=load_prompt(AgentRole.STRESS_TEST),
            task=STRESS_TASK,
            brief_lines=brief + [_menu_line(strategy)] + [_stressor_line(s) for s in menu],
            provider=self._provider,
            routing=self._routing,
            bus=self._bus,
            company=self._company,
            as_of=self._as_of,
            scoped=scoped,
            incident=self._incident,
            investigation_id=self.investigation_id,
            run_id=f"{self.investigation_id}-stress-{strategy.strategy_id}",
        )

        offered = {stressor.stressor_id: stressor for stressor in menu}
        chosen = [
            offered[stressor_id]
            for stressor_id in (selection.value.stressor_ids if selection.value else [])
            if stressor_id in offered
        ]
        fallback_reason = ""
        if not chosen:
            fallback_reason = (
                selection.failure_reason or "the selection named no stressor on the menu"
            )
            # An untested plan is worse than an over-tested one, so the fallback is all
            # of them rather than none.
            chosen = menu
            self._bus.emit(
                SystemDegraded,
                investigation_id=self.investigation_id,
                mark=StatusMark.WARN,
                status_line="stressor selection fell back to the whole menu",
                component="stress_test",
                reason=fallback_reason[:400],
            )

        results = [apply(strategy, stressor, position=position, menu=menu) for stressor in chosen]
        for result in results:
            self._bus.emit(
                StressCompleted,
                investigation_id=self.investigation_id,
                mark=StatusMark.OK if result.passed else StatusMark.WARN,
                status_line=(
                    f"{result.stressor.label}: {result.min_cash} at W{result.min_cash_week}, "
                    f"{'passes' if result.passed else 'fails'}"
                )[:200],
                result=result,
            )

        return StressReport(
            strategy_id=strategy.strategy_id,
            results=results,
            rationale=(
                selection.value.rationale
                if selection.value is not None and not fallback_reason
                else f"Whole menu applied: {fallback_reason}"
            ),
            model_selected=not fallback_reason,
            fallback_reason=fallback_reason,
            uncalibrated=UNCALIBRATED,
        )

    async def _menu(
        self, scoped: ScopedToolset, position: LiquidityPosition
    ) -> tuple[list[Stressor], list[str]]:
        try:
            payload = await scoped.call(
                "get_forecast_error_percentiles", horizon_weeks=position.min_cash_week
            )
        except ToolError as exc:
            return [], [f"measured forecast error unavailable ({exc})"]
        assert isinstance(payload, ForecastErrorPercentiles)

        menu = calibrate(payload, horizon_weeks=position.min_cash_week)
        brief = [
            f"trough is W{position.min_cash_week} at {position.min_cash} against a "
            f"{position.floor} floor",
        ]
        brief += [f"not calibratable -- {name}: {why}" for name, why in UNCALIBRATED.items()]
        return menu, brief


def _stressor_line(stressor: Stressor) -> str:
    return (
        f"{stressor.stressor_id}: {stressor.label}, {stressor.shift_pct}% ({stressor.calibration})"
    )


def _menu_line(strategy: Strategy) -> str:
    kinds = sorted({action.kind.value for action in strategy.actions})
    return f"bundle {strategy.strategy_id} leans on: {', '.join(kinds)}"
