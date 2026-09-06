"""Compose levers into priced strategies and executable worklists.

Agents propose levers. Deterministic code prices, gates, and bundles them. That split is
what keeps financial arithmetic out of the model.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from backend.contracts.agent import ActionKind, AgentFinding, AgentRole, ProposedAction
from backend.contracts.constraints import ConstraintKind, ConstraintSeverity, ConstraintViolation
from backend.contracts.money import Money, money_sum
from backend.contracts.provenance import Evidence, SourceSystem
from backend.contracts.strategy import RejectedAction, Strategy, WorklistItem, WorklistStatus
from backend.tools.results import LiquidityPosition

OWNERS: dict[ActionKind, str] = {
    ActionKind.COLLECTION_CALL: "Collections",
    ActionKind.EARLY_PAY_DISCOUNT: "Collections",
    ActionKind.DISPUTE_RESOLUTION: "Collections",
    ActionKind.AP_DEFER: "AP Manager",
    ActionKind.AP_ACCELERATE: "AP Manager",
    ActionKind.DODO_RETRY: "Revenue Ops",
    ActionKind.DODO_DUNNING: "Revenue Ops",
    ActionKind.REVOLVER_DRAW: "Treasurer",
    ActionKind.ASSUMPTION_REVIEW: "Treasury Analyst",
}


def _action_cash(action: ProposedAction) -> Money:
    if action.amount is not None:
        return action.amount
    return Money.zero("USD")


def _net_impact(actions: list[ProposedAction]) -> Money:
    return money_sum([_action_cash(a) for a in actions], "USD")


def _project_min_cash(base: Money, impact: Money) -> Money:
    return Money(minor_units=base.minor_units + impact.minor_units, currency=base.currency)


def propose_strategy_bundles(actions: list[ProposedAction]) -> list[list[ProposedAction]]:
    """At least four materially different bundles with different risk shapes."""
    ar = [
        a
        for a in actions
        if a.kind
        in {
            ActionKind.COLLECTION_CALL,
            ActionKind.EARLY_PAY_DISCOUNT,
            ActionKind.DISPUTE_RESOLUTION,
        }
    ]
    ap = [a for a in actions if a.kind is ActionKind.AP_DEFER]
    dodo = [a for a in actions if a.kind in {ActionKind.DODO_RETRY, ActionKind.DODO_DUNNING}]

    revolver = ProposedAction(
        kind=ActionKind.REVOLVER_DRAW,
        rationale="Draw revolver to restore policy floor after stress failure",
        amount=Money(minor_units=200_000_000, currency="USD"),  # $2.0M
        evidence_refs=["debt:revolver#available"],
    )

    bundles: list[list[ProposedAction]] = []
    # A — aggressive: all surviving levers (often includes too little after rejections)
    bundles.append(list(actions))
    # B — AR + Dodo only
    bundles.append(ar + dodo)
    # C — AR + AP (no Dodo)
    bundles.append(ar + ap)
    # D — AR + Dodo + modest revolver (the robust replan candidate)
    bundles.append(ar + dodo + [revolver])
    # E — AP + Dodo + smaller revolver
    if ap:
        bundles.append(
            ap
            + dodo
            + [
                ProposedAction(
                    kind=ActionKind.REVOLVER_DRAW,
                    rationale="Smaller draw paired with AP float",
                    amount=Money(minor_units=100_000_000, currency="USD"),
                    evidence_refs=["debt:revolver#available"],
                )
            ]
        )

    # Deduplicate empty / identical bundles while keeping order.
    seen: set[tuple[tuple[str, str | None, int], ...]] = set()
    unique: list[list[ProposedAction]] = []
    for bundle in bundles:
        if not bundle:
            continue
        key = tuple(
            (
                a.kind.value,
                a.document_ref,
                a.amount.minor_units if a.amount else 0,
            )
            for a in bundle
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(bundle)
    return unique[:5] if len(unique) >= 4 else unique


def price_strategies(
    bundles: list[list[ProposedAction]],
    *,
    position: LiquidityPosition,
    names: list[str] | None = None,
) -> list[Strategy]:
    """Price each bundle against current projected min cash. No LLM."""
    default_names = [
        "Aggressive collections + payables",
        "AR + Dodo recovery",
        "AR + AP float",
        "Robust: AR + Dodo + revolver",
        "AP float + revolver",
    ]
    strategies: list[Strategy] = []
    for index, bundle in enumerate(bundles):
        impact = _net_impact(bundle)
        projected = _project_min_cash(position.min_cash, impact)
        violations: list[ConstraintViolation] = []
        if projected < position.floor:
            violations.append(
                ConstraintViolation(
                    constraint_id="min_cash",
                    kind=ConstraintKind.MIN_CASH,
                    severity=ConstraintSeverity.HARD,
                    description=(
                        f"Projected min cash {projected} still below floor {position.floor}"
                    ),
                    observed_money=projected,
                    threshold_display=str(position.floor),
                    week_index=position.min_cash_week,
                )
            )
        for action in bundle:
            if (
                action.kind is ActionKind.REVOLVER_DRAW
                and action.amount is not None
                and action.amount > position.revolver_available
            ):
                violations.append(
                    ConstraintViolation(
                        constraint_id="revolver_capacity",
                        kind=ConstraintKind.MAX_REVOLVER_UTILIZATION,
                        severity=ConstraintSeverity.HARD,
                        description="Proposed draw exceeds undrawn revolver capacity",
                        observed_money=action.amount,
                        threshold_display=str(position.revolver_available),
                    )
                )
        name = (names[index] if names and index < len(names) else None) or (
            default_names[index] if index < len(default_names) else f"Strategy {index + 1}"
        )
        strategies.append(
            Strategy(
                strategy_id=f"strategy-{index + 1}",
                name=name,
                actions=bundle,
                projected_min_cash=projected,
                projected_min_cash_week=position.min_cash_week,
                net_cash_impact=impact,
                financing_cost=_revolver_cost(bundle),
                constraint_violations=violations,
            )
        )
    return strategies


def _revolver_cost(actions: list[ProposedAction]) -> Money:
    draw = money_sum(
        [_action_cash(a) for a in actions if a.kind is ActionKind.REVOLVER_DRAW],
        "USD",
    )
    # Approximate one-month interest at 8% APR on the draw.
    interest = int(Decimal(draw.minor_units) * Decimal("0.08") / Decimal("12"))
    return Money(minor_units=interest, currency="USD")


def compose_worklist(
    strategy: Strategy,
    *,
    as_of: date,
    needs_approval_kinds: frozenset[ActionKind] | None = None,
) -> list[WorklistItem]:
    """Turn the selected strategy into executable rows."""
    approval_kinds = needs_approval_kinds or frozenset(
        {ActionKind.REVOLVER_DRAW, ActionKind.AP_DEFER}
    )
    items: list[WorklistItem] = []
    for seq, action in enumerate(strategy.actions, start=1):
        amount = _action_cash(action)
        status = (
            WorklistStatus.NEEDS_APPROVAL if action.kind in approval_kinds else WorklistStatus.OPEN
        )
        approval_id = (
            f"apr-{strategy.strategy_id}-{seq}" if status is WorklistStatus.NEEDS_APPROVAL else None
        )
        items.append(
            WorklistItem(
                seq=seq,
                owner=OWNERS.get(action.kind, "Treasury Analyst"),
                action=f"{action.kind.value}: {action.rationale}"[:200],
                counterparty=action.counterparty,
                document_ref=action.document_ref,
                amount=amount,
                due_date=action.due_by or (as_of + timedelta(days=7)),
                status=status,
                proposed_by=_proposer(action.kind),
                expected_cash_impact=amount,
                evidence=[
                    Evidence(
                        reference=ref,
                        source=SourceSystem(ref.split(":", 1)[0])
                        if ref.split(":", 1)[0] in set(SourceSystem)
                        else SourceSystem.GL,
                        excerpt=action.rationale[:280],
                    )
                    for ref in action.evidence_refs[:3]
                ]
                or [
                    Evidence(
                        reference="forecast:strategy#selected",
                        source=SourceSystem.FORECAST,
                        excerpt=action.rationale[:280],
                    )
                ],
                approval_request_id=approval_id,
            )
        )
    return items


def _proposer(kind: ActionKind) -> AgentRole:
    if kind in {
        ActionKind.COLLECTION_CALL,
        ActionKind.EARLY_PAY_DISCOUNT,
        ActionKind.DISPUTE_RESOLUTION,
    }:
        return AgentRole.AR_COLLECTIONS
    if kind in {ActionKind.AP_DEFER, ActionKind.AP_ACCELERATE}:
        return AgentRole.AP_OPTIMIZATION
    if kind in {ActionKind.DODO_RETRY, ActionKind.DODO_DUNNING}:
        return AgentRole.DODO_REVENUE
    if kind is ActionKind.REVOLVER_DRAW:
        return AgentRole.COMMANDER
    return AgentRole.FORECAST


def rejected_from_findings(
    findings: list[AgentFinding],
    dropped: list[ProposedAction],
) -> list[RejectedAction]:
    """Surface what we did not recommend, with evidence."""
    out: list[RejectedAction] = []
    supplier = next((f for f in findings if f.agent is AgentRole.SUPPLIER_RISK), None)
    for action in dropped:
        out.append(
            RejectedAction(
                action=action,
                rejected_by="supplier_risk",
                reason="Supplier Risk rejected this deferral on concentration / sole-source evidence",
                evidence=list(supplier.evidence) if supplier else [],
            )
        )
    # Also surface AP actions refused by the AP agent's own review gate.
    for finding in findings:
        if finding.agent is AgentRole.AP_OPTIMIZATION and finding.status.value == "refused":
            for action in finding.recommended_actions:
                out.append(
                    RejectedAction(
                        action=action,
                        rejected_by="ap_optimization",
                        reason=finding.detail or finding.headline,
                        violation=ConstraintViolation(
                            constraint_id="protected_payment_class",
                            kind=ConstraintKind.PROTECTED_PAYMENT_CLASS,
                            severity=ConstraintSeverity.HARD,
                            description=finding.headline,
                            threshold_display="0 days",
                            observed_days=action.delay_days,
                        ),
                        evidence=list(finding.evidence),
                    )
                )
    return out
