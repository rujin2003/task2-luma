"""Supplier Risk Agent -- adversarial by design.

This agent exists to reject other agents' proposals, so it is the only specialist that
takes another agent's output as an input. It is handed the AP agent's actual proposed
deferrals, not a summary of them, because "reject a specific proposal with evidence
attached" is not something you can do from a digest line.

Its rejections are cheap to make and expensive to ignore, which is the right way round: a
sole-source supplier that stops shipping costs more than three weeks of float ever saved.
The agent is adversarial, not obstructive -- the prompt asks it to say plainly which
proposals it does *not* object to, because blanket caution is the same as no review.
"""

from __future__ import annotations

from typing import ClassVar

from backend.contracts.agent import AgentRole, ProposedAction
from backend.tools.registry import ScopedToolset
from backend.tools.results import DeferralCandidates, SupplierRiskProfile
from backend.tools.toolset import ToolError

from .base import Specialist, cite, fetch, money

# The wave hands over a handful of proposals, not a catalogue. A cap keeps one bad plan
# from turning into fifty supplier lookups.
MAX_SUPPLIERS = 6


class SupplierRiskAgent(Specialist):
    role: ClassVar[AgentRole] = AgentRole.SUPPLIER_RISK
    default_task: ClassVar[str] = (
        "Review each proposed deferral against the supplier's risk profile and reject "
        "the ones the profile does not support."
    )

    def __init__(
        self, *, proposals: list[ProposedAction] | None = None, task: str | None = None
    ) -> None:
        super().__init__(task=task)
        self.proposals = proposals or []

    async def gather(self, tools: ScopedToolset) -> list[str]:
        candidates = await fetch(
            tools, "rank_deferral_candidates", DeferralCandidates, top_n=10
        )
        terms = {row.supplier: row for row in candidates.rows}

        lines: list[str] = []
        if not self.proposals:
            lines.append("no deferral has been proposed, so there is nothing to challenge")

        for index, proposal in enumerate(self.proposals, start=1):
            row = terms.get(proposal.counterparty or "")
            lines.append(
                f"proposal {index}: {proposal.kind.value} "
                f"{proposal.counterparty or 'unnamed supplier'} "
                f"{proposal.document_ref or 'unnamed document'} "
                f"{money(proposal.amount)}, delay {proposal.delay_days or 0}d"
                + (f", terms allow {row.max_delay_days}d" if row else "")
            )

        for supplier in self._suppliers():
            try:
                profile = await fetch(
                    tools, "get_supplier_risk_profile", SupplierRiskProfile, supplier=supplier
                )
            except ToolError as exc:
                # A supplier we hold no profile for is not a supplier we can clear.
                lines.append(f"{supplier}: no risk profile available ({exc})")
                continue
            flags = [
                "sole source" if profile.sole_source else "alternates available",
                f"{profile.concentration_pct}% of category spend",
                f"{profile.late_payments_6m} late payments in 6 months",
                f"{profile.open_disputes} open disputes",
                f"{profile.contractual_terms_days}d contractual terms",
            ]
            if profile.on_credit_hold:
                flags.append("ON CREDIT HOLD")
            line = f"{profile.supplier}: " + ", ".join(flags)
            if profile.notes:
                line += f" -- {profile.notes}"
            lines.append(
                cite(line, profile.references[0]) if profile.references else line
            )
        return lines

    def _suppliers(self) -> list[str]:
        """Distinct counterparties in proposal order, capped."""
        seen: list[str] = []
        for proposal in self.proposals:
            name = proposal.counterparty
            if name and name not in seen:
                seen.append(name)
        return seen[:MAX_SUPPLIERS]
