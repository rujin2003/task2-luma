"""The enumerated investigation plans the Commander chooses between.

`PHASES.md` phase 8 asks for selection from an enumerated set rather than free-form
planning, and `LLM_STRATEGY.md` section 6 says why: a small model asked to invent an
investigation produces something plausible and different every time, while the same model
asked to pick one of five and justify it is reliable, auditable and free.

Two things are encoded here that a generated plan could not give you:

* **Every omission carries a written reason.** A plan does not merely list who it invokes;
  it states, per specialist, why the ones it leaves out are the wrong agents for this
  incident. "Why was Dodo not called?" is a question a treasurer will ask, and the answer
  has to be better than silence.
* **Capability gates the catalogue before the model sees it.** A tenant with no Dodo
  connection is never offered a plan that depends on Dodo, so the model cannot select an
  investigation this tenant is incapable of running. The skip reason then names the
  missing source rather than the plan's editorial judgement, because those are different
  facts and the treasurer is owed the true one.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from backend.contracts.agent import AgentRole
from backend.contracts.constraints import ConstraintKind
from backend.contracts.events import SkippedAgent
from backend.contracts.provenance import SourceSystem
from backend.tools.results import CapabilityManifest

# The six specialists, in the order a wave should report them. Coordinating roles are not
# planned for -- the Commander is not one of the agents it dispatches.
SPECIALIST_ROLES: tuple[AgentRole, ...] = (
    AgentRole.FORECAST,
    AgentRole.VARIANCE,
    AgentRole.AR_COLLECTIONS,
    AgentRole.AP_OPTIMIZATION,
    AgentRole.SUPPLIER_RISK,
    AgentRole.DODO_REVENUE,
)

# What each specialist cannot work without. A role whose source is missing is skipped for
# a reason of fact, not of plan design.
ROLE_SOURCES: dict[AgentRole, SourceSystem] = {
    AgentRole.FORECAST: SourceSystem.FORECAST,
    AgentRole.VARIANCE: SourceSystem.FORECAST,
    AgentRole.AR_COLLECTIONS: SourceSystem.AR_LEDGER,
    AgentRole.AP_OPTIMIZATION: SourceSystem.AP_LEDGER,
    AgentRole.SUPPLIER_RISK: SourceSystem.AP_LEDGER,
    AgentRole.DODO_REVENUE: SourceSystem.DODO,
}


class InvestigationPlan(BaseModel):
    """One shape of investigation: who runs, who does not, and why not."""

    model_config = ConfigDict(frozen=True)

    plan_id: Annotated[str, Field(min_length=1)]
    label: Annotated[str, Field(min_length=1, max_length=120)]
    fits: Annotated[str, Field(min_length=1, max_length=280)]
    invokes: tuple[AgentRole, ...] = Field(min_length=1)
    # Written justification per specialist this plan leaves out. Authoring a plan means
    # authoring its refusals; a missing entry is a validation error, not a default.
    omits: dict[AgentRole, str] = Field(default_factory=dict)
    triggers: tuple[ConstraintKind, ...] = ()

    def model_post_init(self, _: object) -> None:
        missing = [
            role.value
            for role in SPECIALIST_ROLES
            if role not in self.invokes and role not in self.omits
        ]
        if missing:
            raise ValueError(f"plan {self.plan_id} omits {', '.join(missing)} without saying why")

    @property
    def required_sources(self) -> frozenset[SourceSystem]:
        return frozenset(ROLE_SOURCES[role] for role in self.invokes if role in ROLE_SOURCES)

    def runnable_with(self, manifest: CapabilityManifest) -> bool:
        return self.required_sources <= set(manifest.available_sources)

    def skips(self, manifest: CapabilityManifest) -> list[SkippedAgent]:
        """Every specialist not invoked, with the true reason it was not.

        A missing source outranks the plan's own judgement: if this tenant has no Dodo
        connection, "no Dodo connection" is the reason, even when the plan would also
        have left it out on the merits.
        """
        available = set(manifest.available_sources)
        skipped: list[SkippedAgent] = []
        for role in SPECIALIST_ROLES:
            if role in self.invokes:
                continue
            source = ROLE_SOURCES.get(role)
            if source is not None and source not in available:
                reason = (
                    f"this tenant has no {source.value} source connected, so "
                    f"{role.value} has nothing to read"
                )
            else:
                reason = self.omits[role]
            skipped.append(SkippedAgent(agent=role, reason=reason))
        return skipped

    def menu_line(self) -> str:
        """How this plan is offered to the Commander. One decision per line."""
        return (
            f"{self.plan_id}: {self.label} -- invokes "
            f"{', '.join(role.value for role in self.invokes)}. Fits when {self.fits}"
        )


PLAN_CATALOGUE: tuple[InvestigationPlan, ...] = (
    InvestigationPlan(
        plan_id="full-liquidity-sweep",
        label="Full liquidity sweep",
        fits="the cash floor is breached and no single driver dominates the shortfall",
        invokes=SPECIALIST_ROLES,
        omits={},
        triggers=(ConstraintKind.MIN_CASH, ConstraintKind.MIN_30D_LIQUIDITY),
    ),
    InvestigationPlan(
        plan_id="receivables-shortfall",
        label="Receivables shortfall",
        fits="the shortfall traces to trade AR: slipped enterprise invoices or a "
        "collection curve that has moved",
        invokes=(AgentRole.FORECAST, AgentRole.VARIANCE, AgentRole.AR_COLLECTIONS),
        omits={
            AgentRole.AP_OPTIMIZATION: (
                "the shortfall is on the receipts side; deferring payables would move cash "
                "without addressing the cause, and costs discount to do it"
            ),
            AgentRole.SUPPLIER_RISK: (
                "no deferral has been proposed, so there is no supplier decision to challenge"
            ),
            AgentRole.DODO_REVENUE: (
                "subscription receipts are on plan; this is a trade-AR incident and Dodo has "
                "no bearing on it"
            ),
        },
        triggers=(ConstraintKind.MIN_CASH,),
    ),
    InvestigationPlan(
        plan_id="payables-pressure",
        label="Payables pressure",
        fits="receipts are on plan and the gap is a payment-run timing or terms question",
        invokes=(
            AgentRole.FORECAST,
            AgentRole.AP_OPTIMIZATION,
            AgentRole.SUPPLIER_RISK,
        ),
        omits={
            AgentRole.VARIANCE: (
                "the bridge already ties and the cause is known; re-explaining it spends a "
                "call on a question nobody asked"
            ),
            AgentRole.AR_COLLECTIONS: (
                "receivables are collecting to curve, so there is no acceleration to rank"
            ),
            AgentRole.DODO_REVENUE: (
                "subscription receipts are on plan; this is an outflow-timing incident"
            ),
        },
        triggers=(ConstraintKind.MIN_CASH, ConstraintKind.MAX_SUPPLIER_DELAY),
    ),
    InvestigationPlan(
        plan_id="subscription-decline",
        label="Subscription revenue decline",
        fits="the miss is concentrated in subscription receipts and payment failure rates "
        "have moved",
        invokes=(AgentRole.FORECAST, AgentRole.VARIANCE, AgentRole.DODO_REVENUE),
        omits={
            AgentRole.AR_COLLECTIONS: (
                "trade AR is collecting to curve; the shortfall is card-present failure, "
                "not an aging problem"
            ),
            AgentRole.AP_OPTIMIZATION: (
                "recovering failed collections is cheaper than buying float with discount"
            ),
            AgentRole.SUPPLIER_RISK: (
                "no deferral has been proposed, so there is no supplier decision to challenge"
            ),
        },
        triggers=(ConstraintKind.MIN_CASH,),
    ),
    InvestigationPlan(
        plan_id="covenant-headroom",
        label="Covenant headroom",
        fits="cash is adequate but a covenant or the revolver ceiling is close to breaching",
        invokes=(
            AgentRole.FORECAST,
            AgentRole.VARIANCE,
            AgentRole.AP_OPTIMIZATION,
            AgentRole.SUPPLIER_RISK,
        ),
        omits={
            AgentRole.AR_COLLECTIONS: (
                "accelerating receipts does not move a leverage or utilization ratio inside "
                "the test window"
            ),
            AgentRole.DODO_REVENUE: (
                "subscription recovery is too small relative to the tested balance to change "
                "the ratio"
            ),
        },
        triggers=(ConstraintKind.COVENANT_RATIO, ConstraintKind.MAX_REVOLVER_UTILIZATION),
    ),
)

PLANS_BY_ID: dict[str, InvestigationPlan] = {plan.plan_id: plan for plan in PLAN_CATALOGUE}

# What the Commander falls back to when the model is unavailable or picks something that
# is not on the menu. The widest plan is the safe default: an investigation that called
# too many agents is expensive, one that called too few is wrong.
DEFAULT_PLAN_ID = "full-liquidity-sweep"


class PlanChoice(BaseModel):
    """The Commander's output schema. A plan id and a reason, and nothing else."""

    model_config = ConfigDict(frozen=True)

    plan_id: Annotated[str, Field(min_length=1)]
    rationale: Annotated[str, Field(min_length=1, max_length=400)]


def available_plans(manifest: CapabilityManifest) -> list[InvestigationPlan]:
    """The catalogue this tenant can actually run, in catalogue order."""
    return [plan for plan in PLAN_CATALOGUE if plan.runnable_with(manifest)]


def plans_for(
    manifest: CapabilityManifest, kinds: Sequence[ConstraintKind] = ()
) -> list[InvestigationPlan]:
    """Runnable plans, narrowed to the breach kinds when any were supplied."""
    runnable = available_plans(manifest)
    if not kinds:
        return runnable
    matching = [plan for plan in runnable if set(plan.triggers) & set(kinds)]
    return matching or runnable


def fallback_plan(manifest: CapabilityManifest) -> InvestigationPlan:
    """The plan used when the model cannot choose. Never returns nothing."""
    runnable = available_plans(manifest)
    for plan in runnable:
        if plan.plan_id == DEFAULT_PLAN_ID:
            return plan
    if runnable:
        # The widest runnable plan: most agents invoked, so fewest blind spots.
        return max(runnable, key=lambda plan: len(plan.invokes))
    raise RuntimeError(
        "no investigation plan is runnable for this tenant; "
        f"available sources: {[s.value for s in manifest.available_sources]}"
    )
