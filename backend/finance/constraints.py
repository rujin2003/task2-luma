"""The hard-constraint gate.

Every proposed action passes through here before it can be priced, stress-tested
or routed for approval. The output is deliberately *structured* — the violated
constraint and the margin by which it was violated — rather than a boolean,
because Phase 8's replan loop needs to know which constraint to tighten and by
how much, and the treasurer needs to see why an action was rejected
(`WORKFLOW.md` §8: showing what was rejected and why is the first thing an
experienced treasurer looks for).

Two properties are load-bearing:

* **Payroll and tax fail closed.** A deferral touching a protected payment class
  is rejected from the policy row, not from a prompt instruction, and not from a
  hard-coded string in an agent. The agent cannot route around it because the
  agent never computes — it calls this.
* **Every constraint is evaluated, always.** The gate does not short-circuit on
  the first failure. An action that breaks three constraints reports three, so a
  replan does not fix one and rediscover the next on the following iteration.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.contracts.common import MoneyDTO
from backend.contracts.constraint import Constraint
from backend.finance.errors import PolicyError
from backend.finance.money import Money
from backend.finance.policy import TreasuryPolicy

#: Actions that move cash out later than contracted, i.e. the ones a protected
#: payment class must never be subject to.
DEFERRING_ACTIONS: frozenset[str] = frozenset({"ap_deferral", "payment_delay", "payroll_delay"})

#: Actions that consume committed facility capacity.
DRAWING_ACTIONS: frozenset[str] = frozenset({"revolver_draw", "term_draw"})


@dataclass(frozen=True, slots=True)
class ProposedAction:
    """A candidate action, as the constraint gate needs to see it."""

    action: str
    amount: Money
    payment_class: str | None = None
    deferral_days: int = 0
    counterparty: str | None = None
    single_source: bool = False
    replacement_lead_time_days: int | None = None


@dataclass(frozen=True, slots=True)
class LiquidityState:
    """Projected liquidity *after* the action, as the engine computed it.

    These are engine outputs, never agent estimates. `covenant_headroom` is
    `None` when no covenant is tested inside the horizon — a covenant that is not
    tested cannot be breached, and pretending otherwise produces the continuously
    evaluated ratio `DATA_SOURCES.md` §3 warns about.
    """

    projected_min_cash: Money
    projected_min_30d_liquidity: Money
    revolver_limit: Money
    revolver_drawn: Money
    covenant_headroom: Money | None = None
    covenant_name: str | None = None

    @property
    def undrawn_revolver(self) -> Money:
        return self.revolver_limit - self.revolver_drawn


def _money(value: Money) -> MoneyDTO:
    return MoneyDTO.from_money(value)


def _floor_constraint(
    name: str,
    actual: Money,
    limit: Money,
    passing_reason: str,
    failing_reason: str,
) -> Constraint:
    """`actual >= limit`, with the margin reported either way."""
    margin = actual - limit
    passed = actual >= limit
    return Constraint(
        name=name,
        passed=passed,
        actual=_money(actual),
        limit=_money(limit),
        margin=_money(margin),
        reason=passing_reason if passed else failing_reason,
    )


def _protected_class(policy: TreasuryPolicy, proposal: ProposedAction) -> Constraint:
    """Payroll and tax delays are forced to zero. This is the fail-closed one."""
    payment_class = proposal.payment_class
    protected = payment_class is not None and policy.is_protected(payment_class)
    defers = proposal.action in DEFERRING_ACTIONS or proposal.deferral_days > 0
    passed = not (protected and defers)
    if passed:
        reason = (
            f"{proposal.action} does not delay a protected payment class"
            if not protected
            else f"payment class '{payment_class}' is protected but the action does not delay it"
        )
    else:
        reason = (
            f"payment class '{payment_class}' is protected by TreasuryPolicy "
            f"v{policy.version} — delaying it is not permitted"
        )
    return Constraint(
        name="protected_payment_class",
        passed=passed,
        actual=proposal.deferral_days,
        limit=0 if protected else None,
        margin=-proposal.deferral_days if not passed else None,
        reason=reason,
    )


def _revolver_capacity(proposal: ProposedAction, state: LiquidityState) -> Constraint:
    """A draw cannot exceed undrawn capacity."""
    undrawn = state.undrawn_revolver
    drawing = proposal.action in DRAWING_ACTIONS
    requested = proposal.amount if drawing else Money.zero(undrawn.currency)
    margin = undrawn - requested
    passed = margin.amount >= 0
    return Constraint(
        name="revolver_capacity",
        passed=passed,
        actual=_money(requested),
        limit=_money(undrawn),
        margin=_money(margin),
        reason=(
            f"draw of {requested} fits within undrawn capacity {undrawn}"
            if passed
            else f"draw of {requested} exceeds undrawn capacity {undrawn} by {-margin}"
        ),
    )


def _revolver_utilization(
    policy: TreasuryPolicy, proposal: ProposedAction, state: LiquidityState
) -> Constraint:
    """Utilization after the action stays inside the policy ceiling."""
    limit_bps = policy.max_revolver_utilization_bps
    if state.revolver_limit.amount == 0:
        return Constraint(
            name="max_revolver_utilization",
            passed=True,
            actual=0,
            limit=limit_bps,
            margin=limit_bps,
            reason="no revolver facility; utilization is not applicable",
        )
    drawing = proposal.action in DRAWING_ACTIONS
    post_draw = state.revolver_drawn + (
        proposal.amount if drawing else Money.zero(state.revolver_drawn.currency)
    )
    utilization_bps = post_draw.amount * 10_000 // state.revolver_limit.amount
    passed = utilization_bps <= limit_bps
    return Constraint(
        name="max_revolver_utilization",
        passed=passed,
        actual=utilization_bps,
        limit=limit_bps,
        margin=limit_bps - utilization_bps,
        reason=(
            f"post-action utilization {utilization_bps} bps is within the {limit_bps} bps ceiling"
            if passed
            else f"post-action utilization {utilization_bps} bps exceeds the "
            f"{limit_bps} bps ceiling"
        ),
    )


def _covenant_headroom(state: LiquidityState) -> Constraint:
    """Headroom on the covenant tested inside the horizon, if there is one."""
    headroom = state.covenant_headroom
    name = state.covenant_name or "covenant"
    if headroom is None:
        return Constraint(
            name="covenant_headroom",
            passed=True,
            actual=None,
            limit=None,
            margin=None,
            reason="no covenant is tested inside the forecast horizon",
        )
    passed = headroom.amount >= 0
    return Constraint(
        name="covenant_headroom",
        passed=passed,
        actual=_money(headroom),
        limit=_money(Money.zero(headroom.currency)),
        margin=_money(headroom),
        reason=(
            f"{name} retains {headroom} of headroom after the action"
            if passed
            else f"{name} is breached by {-headroom} after the action"
        ),
    )


def _supplier_lead_time(proposal: ProposedAction) -> Constraint:
    """A single-source vendor cannot be delayed past its replacement lead time.

    Criticality does not correlate with spend (`DATA_SOURCES.md` §5), so the
    dangerous deferral is usually a small one. Encoding the lead time as a hard
    constraint means the Supplier Risk agent's rejection has arithmetic behind
    it rather than an opinion.
    """
    lead_time = proposal.replacement_lead_time_days
    if not proposal.single_source or lead_time is None or proposal.deferral_days == 0:
        return Constraint(
            name="supplier_lead_time",
            passed=True,
            actual=proposal.deferral_days,
            limit=lead_time,
            margin=None,
            reason="action does not delay a single-source vendor",
        )
    passed = proposal.deferral_days <= lead_time
    counterparty = proposal.counterparty or "vendor"
    return Constraint(
        name="supplier_lead_time",
        passed=passed,
        actual=proposal.deferral_days,
        limit=lead_time,
        margin=lead_time - proposal.deferral_days,
        reason=(
            f"{proposal.deferral_days}-day deferral is inside {counterparty}'s "
            f"{lead_time}-day replacement lead time"
            if passed
            else f"{proposal.deferral_days}-day deferral of single-source {counterparty} "
            f"exceeds its {lead_time}-day replacement lead time"
        ),
    )


def validate(
    policy: TreasuryPolicy,
    proposal: ProposedAction,
    state: LiquidityState,
) -> tuple[Constraint, ...]:
    """Run every constraint. An action is feasible only if all of them pass."""
    currencies = {
        proposal.amount.currency.code,
        state.projected_min_cash.currency.code,
        state.projected_min_30d_liquidity.currency.code,
        state.revolver_limit.currency.code,
        state.revolver_drawn.currency.code,
    }
    if len(currencies) != 1:
        raise PolicyError(f"constraint gate requires a single currency, got {sorted(currencies)}")
    if proposal.deferral_days < 0:
        raise PolicyError("deferral_days cannot be negative")
    if proposal.amount.amount < 0:
        raise PolicyError("proposed action amount must be a non-negative magnitude")

    min_cash = policy.min_unrestricted_cash.to_money()
    min_liquidity = policy.min_30d_liquidity.to_money()
    return (
        _protected_class(policy, proposal),
        _floor_constraint(
            "min_unrestricted_cash",
            state.projected_min_cash,
            min_cash,
            f"projected minimum cash {state.projected_min_cash} holds the {min_cash} floor",
            f"projected minimum cash {state.projected_min_cash} is below the {min_cash} floor",
        ),
        _floor_constraint(
            "min_30d_liquidity",
            state.projected_min_30d_liquidity,
            min_liquidity,
            f"30-day liquidity {state.projected_min_30d_liquidity} holds the {min_liquidity} floor",
            f"30-day liquidity {state.projected_min_30d_liquidity} is below the "
            f"{min_liquidity} floor",
        ),
        _revolver_capacity(proposal, state),
        _revolver_utilization(policy, proposal, state),
        _covenant_headroom(state),
        _supplier_lead_time(proposal),
    )


def failures(results: tuple[Constraint, ...]) -> tuple[Constraint, ...]:
    return tuple(result for result in results if not result.passed)


def passed(results: tuple[Constraint, ...]) -> bool:
    return all(result.passed for result in results)


def explain(results: tuple[Constraint, ...]) -> str:
    """One line per violated constraint, for the rejection column of a worklist."""
    violated = failures(results)
    if not violated:
        return "all constraints satisfied"
    return "; ".join(f"{item.name}: {item.reason}" for item in violated)


__all__ = [
    "DEFERRING_ACTIONS",
    "DRAWING_ACTIONS",
    "LiquidityState",
    "ProposedAction",
    "explain",
    "failures",
    "passed",
    "validate",
]
