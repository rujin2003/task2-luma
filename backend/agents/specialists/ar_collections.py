"""AR Collections Agent -- what will actually arrive, not what is owed.

The failure mode this agent exists to avoid is the one every naive cash forecast makes:
treating open AR as collectible AR. So the brief carries the probability-weighted rows and
the aging total *separately*, with the gap between them stated in the line itself. An agent
that is shown "$8.42M open, $1.66M expected" has no room to quietly mean the first one.

The output is a worklist: one action per invoice, with a counterparty and a document. An
aggregate is not something a collections analyst can pick up on Monday morning.
"""

from __future__ import annotations

from typing import ClassVar

from backend.contracts.agent import AgentRole
from backend.tools.registry import ScopedToolset
from backend.tools.results import ArAgingSummary, CollectionOpportunities

from .base import Specialist, cite, fetch, money


class ArCollectionsAgent(Specialist):
    role: ClassVar[AgentRole] = AgentRole.AR_COLLECTIONS
    default_task: ClassVar[str] = (
        "Propose a ranked collection worklist, one action per invoice worth chasing."
    )

    def __init__(self, *, top_n: int = 10, task: str | None = None) -> None:
        super().__init__(task=task)
        self.top_n = top_n

    async def gather(self, tools: ScopedToolset) -> list[str]:
        opportunities = await fetch(
            tools, "rank_collection_opportunities", CollectionOpportunities, top_n=self.top_n
        )
        aging = await fetch(tools, "get_ar_aging_summary", ArAgingSummary)

        lines = [
            f"open AR {money(opportunities.total_open)}, "
            f"probability-weighted expected {money(opportunities.total_expected)} -- "
            f"the difference is not collectible and must not be forecast",
        ]
        for row in opportunities.rows:
            lines.append(
                cite(
                    f"{row.customer} {row.document_ref}: {money(row.amount)} due {row.due_date}, "
                    f"{row.days_past_due}d past due, {row.probability_pct}% likely -> "
                    f"{money(row.expected_amount)} expected ({row.empirical_basis})",
                    row.reference,
                )
            )
        if opportunities.truncation:
            lines.append(f"opportunities {opportunities.truncation.display()}")

        buckets = ", ".join(
            f"{bucket.label} {money(bucket.amount)} ({bucket.invoice_count} invoices)"
            for bucket in aging.buckets
        )
        # Cite the aging only if the tool gave a reference for it. Inventing a plausible
        # one here would be the same fabrication we reject the model for.
        aging_line = f"aging: {buckets}; total {money(aging.total)}"
        lines.append(cite(aging_line, aging.references[0]) if aging.references else aging_line)
        return lines
