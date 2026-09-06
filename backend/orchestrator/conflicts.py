"""Conflict detection and resolution -- the part that has to be genuine or not exist.

The failure mode this module is written against is theatrical disagreement: agents that
argue because a demo is more impressive when they do. So the split is strict.

**Detection is deterministic.** Whether two agents contradicted each other is a fact about
their outputs, decided by code that can be read and disbelieved. Two kinds are detected:

* *Semantic* -- one agent proposes an action and another names that action in `rejects`.
  The AP agent proposes deferring BILL-8841; Supplier Risk rejects BILL-8841. That is a
  real disagreement about a real row, and it exists because the seeded data contains a
  sole-source supplier with a discount on the same bill.
* *Structural* -- two agents put materially different numbers on the same document. Beyond
  tolerance it is a contradiction; inside tolerance it is rounding, and calling rounding a
  conflict is how you train a treasurer to ignore the conflict panel.

**Resolution is agentic, and it is resolution by evidence.** A scoped follow-up is
dispatched -- one agent, one narrow question about one document -- and the resolver rules
on what that follow-up put on the record. It is explicitly not "pick the higher
confidence": a model that resolves disagreements by comparing self-reported certainty has
added a random number generator to a treasury system.

If the resolver is unavailable, the deterministic fallback upholds the objection. A
rejection that carried evidence stands until something overturns it, and the recorded
resolution says that a rule decided it rather than a judgement -- because those are
different things and the treasurer is entitled to know which one they are reading.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from backend.agents.choice import choose
from backend.agents.context import Incident
from backend.agents.prompts import load_prompt
from backend.agents.provider import LLMProvider
from backend.agents.routing import ModelRouting
from backend.agents.runner import AgentRunner
from backend.agents.specialists import build
from backend.agents.specialists.supplier_risk import SupplierRiskAgent
from backend.contracts.agent import AgentFinding, AgentRole, AgentRun, ProposedAction
from backend.contracts.events import (
    ConflictDetected,
    ConflictKind,
    ConflictResolved,
    FollowupDispatched,
    StatusMark,
    SystemDegraded,
)
from backend.contracts.money import Money
from backend.contracts.provenance import Evidence, SourceSystem
from backend.orchestrator.bus import EventBus
from backend.orchestrator.runs import RunStore
from backend.tools.toolset import Toolset

# Two figures for the same document inside this band are the same figure. Below the
# absolute floor nothing is a conflict, however wide the ratio looks -- a 40% disagreement
# about $900 is not a treasury matter.
STRUCTURAL_TOLERANCE_PCT = Decimal("5")
STRUCTURAL_FLOOR = 5_000_00  # minor units: $5,000

# One follow-up per conflict, and a cap on how many conflicts one investigation chases.
# An investigation that resolves conflicts until it runs out of them never terminates on
# contradictory data, which is exactly the data it will meet.
MAX_FOLLOWUP_DEPTH = 1
MAX_CONFLICTS = 4


class Conflict(BaseModel):
    """A contradiction between two agents about one specific thing."""

    model_config = ConfigDict(frozen=True)

    conflict_id: Annotated[str, Field(min_length=1)]
    kind: ConflictKind
    agents: tuple[AgentRole, AgentRole]
    subject: Annotated[str, Field(min_length=1)]
    description: Annotated[str, Field(min_length=1, max_length=400)]
    delta: Money | None = None
    # The agent whose position is the objection, for semantic conflicts. Resolution
    # falls back to upholding it when the resolver cannot rule.
    objector: AgentRole | None = None
    followup_to: AgentRole | None = None
    question: Annotated[str, Field(min_length=1, max_length=280)]


class Resolution(BaseModel):
    """The resolver's output schema. Which side stands, why, and on which rows."""

    model_config = ConfigDict(frozen=True)

    upheld: AgentRole | None = None
    resolution: Annotated[str, Field(min_length=1, max_length=400)]
    evidence_refs: list[str] = Field(default_factory=list)


@dataclass(slots=True)
class ResolvedConflict:
    """A conflict and how it was settled, including whether a model settled it."""

    conflict: Conflict
    upheld: AgentRole | None
    resolution: str
    evidence: list[Evidence]
    followup: AgentRun | None = None
    model_resolved: bool = True
    fallback_reason: str = ""

    @property
    def overruled(self) -> AgentRole | None:
        if self.upheld is None:
            return None
        other = [role for role in self.conflict.agents if role is not self.upheld]
        return other[0] if other else None


# --- detection ------------------------------------------------------------------------


def detect(findings: list[AgentFinding]) -> list[Conflict]:
    """Every contradiction these findings contain, semantic first. Deterministic."""
    return (_semantic(findings) + _structural(findings))[:MAX_CONFLICTS]


def _semantic(findings: list[AgentFinding]) -> list[Conflict]:
    """One agent proposes; another names the same row in `rejects`."""
    proposals = {
        action.document_ref: (finding.agent, action)
        for finding in findings
        for action in finding.recommended_actions
        if action.document_ref
    }

    conflicts: list[Conflict] = []
    for finding in findings:
        for rejected in finding.rejects:
            entry = proposals.get(rejected)
            if entry is None or entry[0] is finding.agent:
                continue
            proposer, action = entry
            conflicts.append(
                Conflict(
                    conflict_id=f"conflict-{rejected}",
                    kind=ConflictKind.SEMANTIC,
                    agents=(proposer, finding.agent),
                    subject=rejected,
                    description=(
                        f"{proposer.value} proposes {action.kind.value} on {rejected} "
                        f"({action.counterparty or 'unnamed counterparty'}"
                        f"{f', {action.delay_days}d' if action.delay_days else ''}); "
                        f"{finding.agent.value} rejects it"
                    )[:400],
                    objector=finding.agent,
                    followup_to=finding.agent,
                    question=(
                        f"On {rejected} specifically: which rows in the counterparty's "
                        f"profile support rejecting this {action.kind.value}, and is there "
                        f"a shorter delay the profile does support?"
                    )[:280],
                )
            )
    return conflicts


def _structural(findings: list[AgentFinding]) -> list[Conflict]:
    """Two agents put materially different numbers on the same document."""
    by_document: dict[str, list[tuple[AgentRole, ProposedAction]]] = {}
    for finding in findings:
        for action in finding.recommended_actions:
            if action.document_ref and action.amount is not None:
                by_document.setdefault(action.document_ref, []).append((finding.agent, action))

    conflicts: list[Conflict] = []
    for document, entries in by_document.items():
        if len(entries) < 2:
            continue
        (left_agent, left), (right_agent, right) = entries[0], entries[1]
        if left_agent is right_agent or left.amount is None or right.amount is None:
            continue
        delta = left.amount - right.amount
        if not _material_disagreement(left.amount, right.amount):
            continue
        conflicts.append(
            Conflict(
                conflict_id=f"conflict-amount-{document}",
                kind=ConflictKind.STRUCTURAL,
                agents=(left_agent, right_agent),
                subject=document,
                description=(
                    f"{left_agent.value} puts {left.amount} on {document} while "
                    f"{right_agent.value} puts {right.amount} on it, a {abs(delta)} gap"
                )[:400],
                # The side with less on the record is the side asked to put more on it.
                followup_to=_thinner_case(findings, left_agent, right_agent, document),
                question=(
                    f"On {document} specifically: which source rows support your figure, "
                    f"and what accounts for the {abs(delta)} difference?"
                )[:280],
                delta=delta,
            )
        )
    return conflicts


def _material_disagreement(left: Money, right: Money) -> bool:
    """Beyond tolerance it is a contradiction; inside it, it is rounding."""
    gap = abs(left.minor_units - right.minor_units)
    if gap < STRUCTURAL_FLOOR:
        return False
    larger = max(abs(left.minor_units), abs(right.minor_units))
    if larger == 0:
        return False
    return Decimal(gap) * 100 / Decimal(larger) > STRUCTURAL_TOLERANCE_PCT


def _thinner_case(
    findings: list[AgentFinding], left: AgentRole, right: AgentRole, document: str
) -> AgentRole:
    def cited(role: AgentRole) -> int:
        return sum(
            1
            for finding in findings
            if finding.agent is role
            for evidence in finding.evidence
            if document in evidence.reference
        )

    return left if cited(left) <= cited(right) else right


# --- resolution -----------------------------------------------------------------------


class ConflictResolver:
    """Runs the scoped follow-up and rules on what it produced."""

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

    async def resolve_all(
        self, conflicts: list[Conflict], findings: list[AgentFinding]
    ) -> list[ResolvedConflict]:
        return [await self.resolve(conflict, findings) for conflict in conflicts[:MAX_CONFLICTS]]

    async def resolve(self, conflict: Conflict, findings: list[AgentFinding]) -> ResolvedConflict:
        self._bus.emit(
            ConflictDetected,
            investigation_id=self.investigation_id,
            status_line=f"Conflict on {conflict.subject}: {conflict.description}"[:200],
            conflict_id=conflict.conflict_id,
            kind=conflict.kind,
            agents=list(conflict.agents),
            description=conflict.description,
            delta=conflict.delta,
        )

        positions = [f for f in findings if f.agent in conflict.agents]
        followup = await self._followup(conflict, positions)
        material = positions + ([followup.finding] if followup and followup.finding else [])
        allowed = {
            evidence.reference: evidence for finding in material for evidence in finding.evidence
        }

        ruling = await choose(
            Resolution,
            role=AgentRole.CONFLICT_RESOLUTION,
            system_prompt=load_prompt(AgentRole.CONFLICT_RESOLUTION),
            task=conflict.question,
            brief_lines=_brief(conflict, positions, followup),
            provider=self._provider,
            routing=self._routing,
            bus=self._bus,
            company=self._company,
            as_of=self._as_of,
            incident=self._incident,
            investigation_id=self.investigation_id,
            prior_findings=material,
            run_id=f"{conflict.conflict_id}-resolver",
        )

        resolved = self._rule(conflict, ruling.value, ruling.failure_reason, allowed, followup)
        self._bus.emit(
            ConflictResolved,
            investigation_id=self.investigation_id,
            status_line=f"Resolved {conflict.subject}: {resolved.resolution}"[:200],
            conflict_id=conflict.conflict_id,
            resolution=resolved.resolution,
            upheld=resolved.upheld,
            evidence=resolved.evidence,
        )
        return resolved

    async def _followup(self, conflict: Conflict, positions: list[AgentFinding]) -> AgentRun | None:
        """One agent, one narrow question, one fresh look at the contested rows."""
        target = conflict.followup_to
        if target is None or MAX_FOLLOWUP_DEPTH < 1:
            return None

        self._bus.emit(
            FollowupDispatched,
            investigation_id=self.investigation_id,
            status_line=f"{target.value}: follow-up on {conflict.subject}",
            conflict_id=conflict.conflict_id,
            agent=target,
            question=conflict.question,
        )

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
        try:
            run = await runner.run(
                followup_spec(conflict, positions),
                run_id=f"{conflict.conflict_id}-followup",
            )
        except Exception as exc:
            # A follow-up that fails leaves the conflict to the deterministic rule. It
            # does not take the investigation down with it.
            self._bus.emit(
                SystemDegraded,
                investigation_id=self.investigation_id,
                mark=StatusMark.WARN,
                status_line=f"follow-up on {conflict.subject} failed",
                component=target.value,
                reason=f"{type(exc).__name__}: {exc}"[:400],
            )
            return None
        if self._store is not None:
            self._store.add(run)
        return run

    def _rule(
        self,
        conflict: Conflict,
        ruling: Resolution | None,
        failure_reason: str,
        allowed: dict[str, Evidence],
        followup: AgentRun | None,
    ) -> ResolvedConflict:
        cited = [allowed[ref] for ref in (ruling.evidence_refs if ruling else []) if ref in allowed]

        if ruling is None or not cited:
            reason = failure_reason or (
                "the resolution cited no reference that resolves to a row anyone produced"
            )
            return self._default(conflict, allowed, followup, reason)

        if ruling.upheld is not None and ruling.upheld not in conflict.agents:
            return self._default(
                conflict,
                allowed,
                followup,
                f"the resolver upheld {ruling.upheld.value}, who is not party to this conflict",
            )

        return ResolvedConflict(
            conflict=conflict,
            upheld=ruling.upheld,
            resolution=ruling.resolution,
            evidence=cited,
            followup=followup,
        )

    def _default(
        self,
        conflict: Conflict,
        allowed: dict[str, Evidence],
        followup: AgentRun | None,
        reason: str,
    ) -> ResolvedConflict:
        """The rule that applies when no model ruled. Stated as a rule, not as judgement."""
        self._bus.emit(
            SystemDegraded,
            investigation_id=self.investigation_id,
            mark=StatusMark.WARN,
            status_line=f"{conflict.subject} settled by rule, not by evidence",
            component="conflict_resolution",
            reason=reason[:400],
        )
        upheld = conflict.objector
        if upheld is not None:
            resolution = (
                f"Settled by rule, not by evidence: {upheld.value}'s objection to "
                f"{conflict.subject} stands because it was evidenced and nothing overturned "
                f"it. A supplier that stops shipping costs more than the float this raises."
            )
        else:
            resolution = (
                f"Unresolved: the follow-up on {conflict.subject} did not produce a row that "
                f"settles the disagreement, so neither figure is used and the item is held "
                f"for review."
            )
        return ResolvedConflict(
            conflict=conflict,
            upheld=upheld,
            resolution=resolution[:400],
            # A default still cites the rows the positions rested on, so the treasurer can
            # read the same evidence the rule declined to weigh.
            evidence=list(allowed.values())[:3] or [_unevidenced(conflict)],
            followup=followup,
            model_resolved=False,
            fallback_reason=reason,
        )


def followup_spec(conflict: Conflict, positions: list[AgentFinding]):
    """The specialist that answers one conflict's follow-up, scoped to its subject.

    Public because the recorder builds the same spec to capture its fingerprint. A
    follow-up whose scope drifted from the one that was recorded would replay a broader
    answer than the question deserved, and the evidence validator would be the thing that
    caught it -- late, and looking like a hallucination.
    """
    if conflict.followup_to is AgentRole.SUPPLIER_RISK:
        return SupplierRiskAgent(proposals=contested(conflict, positions), task=conflict.question)
    assert conflict.followup_to is not None
    return build(conflict.followup_to, task=conflict.question)


def contested(conflict: Conflict, positions: list[AgentFinding]) -> list[ProposedAction]:
    """The exact proposals the conflict is about, for a follow-up that must see them."""
    return [
        action
        for finding in positions
        for action in finding.recommended_actions
        if action.document_ref == conflict.subject
    ]


def _brief(
    conflict: Conflict, positions: list[AgentFinding], followup: AgentRun | None
) -> list[str]:
    """The whole material the resolver rules on. One line per fact, no narrative."""
    lines = [f"contradiction ({conflict.kind.value}) on {conflict.subject}: {conflict.description}"]
    for finding in positions:
        lines.append(f"{finding.agent.value} position: {finding.headline}")
        for evidence in finding.evidence:
            lines.append(f"{finding.agent.value} cites [{evidence.reference}] {evidence.excerpt}")
    if followup is not None and followup.finding is not None:
        lines.append(f"follow-up ({followup.agent.value}): {followup.finding.headline}")
        for evidence in followup.finding.evidence:
            lines.append(f"follow-up cites [{evidence.reference}] {evidence.excerpt}")
    elif followup is not None:
        lines.append(f"follow-up did not complete: {followup.status.value}")
    else:
        lines.append("no follow-up was run")
    return lines


def _unevidenced(conflict: Conflict) -> Evidence:
    """`ConflictResolved` requires evidence; an unevidenced resolution says exactly that."""
    return Evidence(
        reference=f"override:{conflict.conflict_id}",
        source=SourceSystem.OVERRIDE,
        excerpt="Settled by rule; no source row was produced by either side.",
    )
