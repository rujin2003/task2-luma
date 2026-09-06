"""Conflict detection is deterministic; resolution prefers evidenced rejections."""

from __future__ import annotations

from dataclasses import dataclass

from backend.contracts.agent import ActionKind, AgentFinding, AgentRole, ProposedAction
from backend.contracts.events import ConflictKind
from backend.contracts.provenance import Evidence


@dataclass(frozen=True, slots=True)
class Conflict:
    conflict_id: str
    kind: ConflictKind
    agents: tuple[AgentRole, ...]
    description: str
    rejected_refs: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True, slots=True)
class ConflictResolution:
    conflict_id: str
    resolution: str
    upheld: AgentRole
    evidence: list[Evidence]
    dropped_actions: list[ProposedAction]


def detect_conflicts(findings: list[AgentFinding]) -> list[Conflict]:
    """Surface numeric disagreements and AP-vs-Supplier-Risk semantic clashes."""
    by_role = {f.agent: f for f in findings}
    conflicts: list[Conflict] = []

    ap = by_role.get(AgentRole.AP_OPTIMIZATION)
    supplier = by_role.get(AgentRole.SUPPLIER_RISK)
    if ap is not None and supplier is not None and supplier.rejects:
        rejected = set(supplier.rejects)
        overlapping = [
            action
            for action in ap.recommended_actions
            if action.document_ref is not None and action.document_ref in rejected
        ]
        if overlapping:
            conflicts.append(
                Conflict(
                    conflict_id="conflict-ap-supplier-1",
                    kind=ConflictKind.SEMANTIC,
                    agents=(AgentRole.AP_OPTIMIZATION, AgentRole.SUPPLIER_RISK),
                    description=(
                        f"AP proposes deferring {', '.join(sorted(rejected))}; "
                        "Supplier Risk rejects with concentration evidence"
                    ),
                    rejected_refs=tuple(sorted(rejected)),
                    evidence=tuple(supplier.evidence),
                )
            )

    # Structural: AR and Dodo both quantify "cash at risk" and disagree beyond $250k.
    ar = by_role.get(AgentRole.AR_COLLECTIONS)
    dodo = by_role.get(AgentRole.DODO_REVENUE)
    if (
        ar is not None
        and dodo is not None
        and ar.quantum is not None
        and dodo.quantum is not None
        and ar.quantum.currency == dodo.quantum.currency
    ):
        delta = abs(ar.quantum.minor_units - dodo.quantum.minor_units)
        if delta > 25_000_000:  # $250k
            conflicts.append(
                Conflict(
                    conflict_id="conflict-ar-dodo-quantum",
                    kind=ConflictKind.STRUCTURAL,
                    agents=(AgentRole.AR_COLLECTIONS, AgentRole.DODO_REVENUE),
                    description=(
                        f"AR quantum {ar.quantum} and Dodo quantum {dodo.quantum} "
                        "differ beyond $250k; they measure different cohorts and must not be summed"
                    ),
                    evidence=tuple(list(ar.evidence[:1]) + list(dodo.evidence[:1])),
                )
            )

    return conflicts


def resolve_conflict(conflict: Conflict, findings: list[AgentFinding]) -> ConflictResolution:
    """Uphold the agent that attached rejection evidence; never pick higher confidence."""
    by_role = {f.agent: f for f in findings}

    if conflict.kind is ConflictKind.SEMANTIC:
        supplier = by_role[AgentRole.SUPPLIER_RISK]
        ap = by_role[AgentRole.AP_OPTIMIZATION]
        rejected = set(conflict.rejected_refs)
        dropped = [
            action
            for action in ap.recommended_actions
            if action.document_ref is not None and action.document_ref in rejected
        ]
        return ConflictResolution(
            conflict_id=conflict.conflict_id,
            resolution=(
                "Uphold Supplier Risk: sole-source / concentration evidence blocks the "
                f"deferred documents {', '.join(sorted(rejected))}"
            ),
            upheld=AgentRole.SUPPLIER_RISK,
            evidence=list(supplier.evidence) or list(conflict.evidence),
            dropped_actions=dropped,
        )

    # Structural: keep both findings; do not merge quantums. No actions dropped.
    return ConflictResolution(
        conflict_id=conflict.conflict_id,
        resolution=(
            "Both findings stand as separate cohorts; Commander will not sum AR and Dodo quantums"
        ),
        upheld=AgentRole.VARIANCE if AgentRole.VARIANCE in by_role else conflict.agents[0],
        evidence=list(conflict.evidence)
        or [
            e
            for role in conflict.agents
            for e in (by_role[role].evidence if role in by_role else [])
        ][:2],
        dropped_actions=[],
    )


def surviving_actions(
    findings: list[AgentFinding],
    resolutions: list[ConflictResolution],
) -> list[ProposedAction]:
    """Levers that survived conflict resolution, ready for scenario composition."""
    dropped_refs: set[str] = set()
    for resolution in resolutions:
        for action in resolution.dropped_actions:
            if action.document_ref:
                dropped_refs.add(action.document_ref)

    actions: list[ProposedAction] = []
    for finding in findings:
        if finding.agent is AgentRole.SUPPLIER_RISK:
            continue
        for action in finding.recommended_actions:
            if action.document_ref and action.document_ref in dropped_refs:
                continue
            if action.kind is ActionKind.ASSUMPTION_REVIEW:
                continue
            actions.append(action)
    return actions
