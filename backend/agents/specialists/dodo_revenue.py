"""Dodo Revenue Agent -- at-risk versus recoverable, under a documented taxonomy.

The distinction that matters is soft versus hard, and it is Dodo's, not ours. A soft
decline (insufficient funds, expired card) is a timing problem with a retry window. A hard
decline (stolen card, closed account) is a closed door, and retrying it is a fee with no
upside -- which is exactly the mistake an agent makes if it is handed a total instead of a
taxonomy.

So the brief is per code, with the recoverable amount and the retry window the tool states.
The recovery rate is never the agent's to estimate.
"""

from __future__ import annotations

from typing import ClassVar

from backend.contracts.agent import AgentRole
from backend.tools.registry import ScopedToolset
from backend.tools.results import DodoDeclineBreakdown

from .base import Specialist, cite, fetch, money


class DodoRevenueAgent(Specialist):
    role: ClassVar[AgentRole] = AgentRole.DODO_REVENUE
    default_task: ClassVar[str] = (
        "Quantify revenue at risk and the part recoverable inside documented retry windows."
    )

    def __init__(self, *, top_n: int = 10, task: str | None = None) -> None:
        super().__init__(task=task)
        self.top_n = top_n

    async def gather(self, tools: ScopedToolset) -> list[str]:
        breakdown = await fetch(
            tools, "get_dodo_decline_breakdown", DodoDeclineBreakdown, top_n=self.top_n
        )

        lines = [
            f"declines: {money(breakdown.at_risk_total)} at risk, "
            f"{money(breakdown.recoverable_total)} recoverable under Dodo's taxonomy",
        ]
        for row in breakdown.rows:
            window = (
                f"retry window {row.retry_window_days}d"
                if row.retry_window_days is not None
                else "no retry window: a retry here is a fee with no upside"
            )
            lines.append(
                cite(
                    f"{row.code} ({row.kind}): {row.count} declines, {money(row.amount)} "
                    f"at risk, {money(row.recoverable_amount)} recoverable, {window}",
                    row.reference,
                )
            )
        if breakdown.truncation:
            lines.append(f"decline codes {breakdown.truncation.display()}")
        return lines
