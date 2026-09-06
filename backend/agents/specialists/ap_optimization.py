"""AP Optimization Agent -- payment timing, with the cost of it stated.

Two things make this agent trustworthy rather than merely useful.

**The discount forgone is priced in every line.** Deferring a bill under 2/10 net 30 is not
free liquidity; it is borrowing at a rate most treasurers would never agree to if it were
quoted to them. So it is quoted to them.

**The refusal is deterministic.** Payroll and statutory tax appear in the candidate list
marked `protected` -- visible, so the agent knows they exist and are not levers -- and
`review()` takes any proposal that touches one off the agent by code. The prompt asks for a
refusal; the gate guarantees one. That distinction is the whole difference between a
treasury system and a chatbot with a policy paragraph.
"""

from __future__ import annotations

from typing import ClassVar

from backend.contracts.agent import (
    ActionKind,
    AgentFinding,
    AgentRole,
    AgentStatus,
    ProposedAction,
)
from backend.contracts.constraints import Constraint, ConstraintKind
from backend.tools.registry import ScopedToolset
from backend.tools.results import DeferralCandidates, PolicyConstraints

from .base import Specialist, cite, evidence_for, fetch, money


class ApOptimizationAgent(Specialist):
    role: ClassVar[AgentRole] = AgentRole.AP_OPTIMIZATION
    default_task: ClassVar[str] = (
        "Propose deferrals from the candidate rows, pricing every discount forgone."
    )

    def __init__(
        self, *, max_delay_days: int = 30, top_n: int = 10, task: str | None = None
    ) -> None:
        super().__init__(task=task)
        self.max_delay_days = max_delay_days
        self.top_n = top_n
        # Populated by gather, read by review. One specialist instance runs one agent once.
        self._protected: dict[str, str] = {}
        self._protection: Constraint | None = None

    async def gather(self, tools: ScopedToolset) -> list[str]:
        policy = await fetch(tools, "get_policy_constraints", PolicyConstraints)
        candidates = await fetch(
            tools,
            "rank_deferral_candidates",
            DeferralCandidates,
            top_n=self.top_n,
            max_delay_days=self.max_delay_days,
        )

        self._protected = {}
        self._protection = next(
            (
                constraint
                for constraint in policy.constraints
                if constraint.kind is ConstraintKind.PROTECTED_PAYMENT_CLASS
            ),
            None,
        )

        lines = [f"total deferrable {money(candidates.total_deferrable)}"]
        for constraint in policy.constraints:
            lines.append(
                f"policy {constraint.constraint_id} ({constraint.severity.value}): "
                f"{constraint.description}"
            )

        for row in candidates.rows:
            if row.protected:
                self._protected[row.document_ref] = row.payment_class
                self._protected[row.reference] = row.payment_class
                self._protected[row.supplier] = row.payment_class
                lines.append(
                    cite(
                        f"PROTECTED, not a lever: {row.supplier} {row.document_ref} "
                        f"{money(row.amount)} due {row.due_date} ({row.payment_class})",
                        row.reference,
                    )
                )
                continue
            discount = (
                f", forgoes {money(row.discount_forgone)} of early-pay discount"
                if row.discount_forgone is not None
                else ", no early-pay discount at stake"
            )
            lines.append(
                cite(
                    f"{row.supplier} {row.document_ref}: {money(row.amount)} due {row.due_date}, "
                    f"deferrable up to {row.max_delay_days}d{discount}",
                    row.reference,
                )
            )

        if candidates.truncation:
            lines.append(f"candidates {candidates.truncation.display()}")
        return lines

    def review(self, finding: AgentFinding) -> AgentFinding:
        """Policy has the last word: a plan that defers a protected class is not a plan."""
        offending = [
            action
            for action in finding.recommended_actions
            if action.kind is ActionKind.AP_DEFER and self._touches_protected(action)
        ]
        if not offending:
            return finding

        constraint = self._protection
        constraint_id = constraint.constraint_id if constraint else "protected-payment-class"
        applies_to = constraint.applies_to if constraint else "payroll"
        documents = ", ".join(
            action.document_ref or "an unnamed document" for action in offending
        )
        evidence = list(finding.evidence)
        if constraint is not None and constraint.source_ref:
            evidence = [evidence_for(constraint.source_ref, constraint.description)]

        return finding.model_copy(
            update={
                "status": AgentStatus.REFUSED,
                "headline": f"Refused: {constraint_id} protects {applies_to}",
                "detail": (
                    f"The proposal defers {documents}, which is a protected payment class "
                    f"under {constraint_id}. Protected classes are not levers, so this is "
                    f"returned as a constraint violation rather than a payment plan. To "
                    f"reach the same liquidity, widen the trade candidates or draw on the "
                    f"revolver."
                )[:1200],
                "evidence": evidence,
                # The whole proposal goes, not just the offending row: a plan that only
                # works because payroll slipped is not a plan once payroll is put back.
                "recommended_actions": [],
                "risks": [
                    "Without the protected class the deferral pool is smaller than the target"
                ],
            }
        )

    def _touches_protected(self, action: ProposedAction) -> bool:
        """Match on any handle the model had: the document, the counterparty, the citation."""
        named = [action.document_ref, action.counterparty, *action.evidence_refs]
        return any(handle in self._protected for handle in named if handle)
