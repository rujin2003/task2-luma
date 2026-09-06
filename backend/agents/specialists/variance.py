"""Variance Agent -- the one that answers the Treasurer's first question.

"Receipts were $1.9M light" is a number they already had. What they do not have is *why*,
and whether it repeats. So the brief this agent gets is built to make root-causing possible
and guessing hard: the bridge rows with the engine's own materiality flag, the drivers that
have gone stale (the usual cause of a repeating miss), and nothing else.

The total delta is handed over deliberately, so the agent never has cause to add the rows
up itself.
"""

from __future__ import annotations

from typing import ClassVar

from backend.contracts.agent import AgentRole
from backend.tools.registry import ScopedToolset
from backend.tools.results import DriverAssumptions, LiquidityPosition, VarianceBridge

from .base import Specialist, cite, fetch, money


class VarianceAgent(Specialist):
    role: ClassVar[AgentRole] = AgentRole.VARIANCE
    default_task: ClassVar[str] = (
        "Root-cause each material delta in the bridge and say which causes repeat."
    )

    def __init__(self, *, week_ending: str | None = None, task: str | None = None) -> None:
        super().__init__(task=task)
        self.week_ending = week_ending

    async def gather(self, tools: ScopedToolset) -> list[str]:
        bridge = await fetch(
            tools,
            "get_variance_bridge",
            VarianceBridge,
            week_ending=self.week_ending,
            top_n=10,
        )
        position = await fetch(tools, "get_liquidity_position", LiquidityPosition)
        # Stale drivers only: a driver that was refreshed on Sunday is not why Monday
        # missed, and the ones that were not are the most common cause of a repeat.
        stale = await fetch(
            tools, "list_driver_assumptions", DriverAssumptions, top_n=10, stale_only=True
        )

        lines = [
            f"bridge for the week ending {bridge.week_ending}: "
            f"total delta {money(bridge.total_delta)}",
        ]
        for row in bridge.rows:
            flag = "MATERIAL" if row.material else "immaterial"
            lines.append(
                cite(
                    f"[{flag}] {row.category}: plan {money(row.plan)}, "
                    f"actual {money(row.actual)}, delta {money(row.delta)}",
                    row.reference,
                )
            )
        if bridge.truncation:
            lines.append(f"bridge rows {bridge.truncation.display()}")

        for driver in stale.rows:
            lines.append(
                cite(
                    f"stale driver {driver.category}: {driver.driver} = "
                    f"{driver.value_display}, last refreshed {driver.last_refreshed} "
                    f"({driver.days_since_refresh}d ago)",
                    driver.reference,
                )
            )
        if not stale.rows:
            lines.append("no driver is stale, so no delta is explained by an unrefreshed driver")

        lines.append(
            f"position: cash today {money(position.cash_today)}, "
            f"minimum {money(position.min_cash)} at W{position.min_cash_week} "
            f"against a {money(position.floor)} floor"
        )
        return lines
