"""Enumerated investigation plans.

The Commander selects from this set rather than inventing a free-form roster. That keeps
planning reliable on a small model and makes "why was Dodo not called" a testable claim.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.contracts.agent import AgentRole
from backend.contracts.constraints import ConstraintKind, ConstraintViolation
from backend.contracts.events import SkippedAgent
from backend.contracts.provenance import SourceSystem
from backend.tools.results import CapabilityManifest


@dataclass(frozen=True, slots=True)
class InvestigationPlan:
    plan_id: str
    label: str
    invoked: tuple[AgentRole, ...]
    skip_reasons: dict[AgentRole, str]


# Full liquidity breach: every specialist that can move the trough.
LIQUIDITY_BREACH_FULL = InvestigationPlan(
    plan_id="liquidity-breach-full",
    label="Full liquidity investigation",
    invoked=(
        AgentRole.FORECAST,
        AgentRole.VARIANCE,
        AgentRole.AR_COLLECTIONS,
        AgentRole.AP_OPTIMIZATION,
        AgentRole.DODO_REVENUE,
    ),
    skip_reasons={},
)

# Pure AR shortfall: no Dodo, no AP wave.
AR_SHORTFALL = InvestigationPlan(
    plan_id="ar-shortfall",
    label="AR-only shortfall investigation",
    invoked=(
        AgentRole.FORECAST,
        AgentRole.VARIANCE,
        AgentRole.AR_COLLECTIONS,
    ),
    skip_reasons={
        AgentRole.AP_OPTIMIZATION: "Incident is AR collections shortfall; AP levers are out of scope",
        AgentRole.SUPPLIER_RISK: "No AP deferrals proposed in an AR-only plan",
        AgentRole.DODO_REVENUE: "Incident is pure trade-AR; Dodo subscription signal is not implicated",
    },
)

DODO_COLLECTION_STRESS = InvestigationPlan(
    plan_id="dodo-collection-stress",
    label="Dodo collection-stress investigation",
    invoked=(
        AgentRole.FORECAST,
        AgentRole.VARIANCE,
        AgentRole.DODO_REVENUE,
        AgentRole.AR_COLLECTIONS,
    ),
    skip_reasons={
        AgentRole.AP_OPTIMIZATION: "Dodo success-rate drop does not open AP deferral as a first lever",
        AgentRole.SUPPLIER_RISK: "No AP deferrals proposed in a Dodo-led plan",
    },
)

PLANS: dict[str, InvestigationPlan] = {
    plan.plan_id: plan for plan in (LIQUIDITY_BREACH_FULL, AR_SHORTFALL, DODO_COLLECTION_STRESS)
}


def select_plan(
    *,
    breach: ConstraintViolation,
    manifest: CapabilityManifest,
    incident_kind: str | None = None,
) -> InvestigationPlan:
    """Pick an enumerated plan from the breach and the tenant's capabilities."""
    if incident_kind == "ar_shortfall":
        plan = AR_SHORTFALL
    elif incident_kind == "dodo_stress" or breach.kind is ConstraintKind.MIN_30D_LIQUIDITY:
        plan = DODO_COLLECTION_STRESS
    else:
        plan = LIQUIDITY_BREACH_FULL

    has_dodo = SourceSystem.DODO in manifest.available_sources
    invoked = list(plan.invoked)
    skipped: dict[AgentRole, str] = dict(plan.skip_reasons)

    if AgentRole.DODO_REVENUE in invoked and not has_dodo:
        invoked.remove(AgentRole.DODO_REVENUE)
        skipped[AgentRole.DODO_REVENUE] = (
            "Tenant capability manifest has no Dodo source; skipping Dodo Revenue agent"
        )

    return InvestigationPlan(
        plan_id=plan.plan_id,
        label=plan.label,
        invoked=tuple(invoked),
        skip_reasons=skipped,
    )


def skipped_agents(plan: InvestigationPlan) -> list[SkippedAgent]:
    """All six specialists not invoked, with a reason for each."""
    all_specialists = {
        AgentRole.FORECAST,
        AgentRole.VARIANCE,
        AgentRole.AR_COLLECTIONS,
        AgentRole.AP_OPTIMIZATION,
        AgentRole.SUPPLIER_RISK,
        AgentRole.DODO_REVENUE,
    }
    invoked = set(plan.invoked)
    # Supplier Risk is dispatched after AP in the investigation runner, not in the plan roster.
    if AgentRole.AP_OPTIMIZATION in invoked:
        invoked.add(AgentRole.SUPPLIER_RISK)

    out: list[SkippedAgent] = []
    for role in sorted(all_specialists, key=lambda r: r.value):
        if role in invoked:
            continue
        reason = plan.skip_reasons.get(
            role,
            f"{role.value} not required for plan {plan.plan_id}",
        )
        out.append(SkippedAgent(agent=role, reason=reason))
    return out
