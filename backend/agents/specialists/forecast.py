"""Forecast Agent -- explains the forecast, and never computes it.

The useful output here is not the number, which the engine already produced and the screen
already shows. It is *what the number assumes*, and which of those assumptions has gone
stale. A forecast that is wrong and a forecast nobody knew was wrong are different
problems, and only the second one is this agent's fault to prevent.
"""

from __future__ import annotations

from typing import ClassVar

from backend.contracts.agent import AgentRole
from backend.tools.registry import ScopedToolset
from backend.tools.results import DriverAssumptions, ForecastSummary, LiquidityPosition

from .base import Specialist, cite, fetch, money


class ForecastAgent(Specialist):
    role: ClassVar[AgentRole] = AgentRole.FORECAST
    default_task: ClassVar[str] = (
        "Explain what each material category assumes and flag every stale driver."
    )

    async def gather(self, tools: ScopedToolset) -> list[str]:
        position = await fetch(tools, "get_liquidity_position", LiquidityPosition)
        summary = await fetch(tools, "get_forecast_summary", ForecastSummary)
        drivers = await fetch(tools, "list_driver_assumptions", DriverAssumptions, top_n=10)

        lines = [
            f"position: cash today {money(position.cash_today)}, "
            f"minimum {money(position.min_cash)} at W{position.min_cash_week} "
            f"against a {money(position.floor)} floor, "
            f"runway {position.runway_weeks} weeks",
            f"forecast {summary.version_id} "
            f"({'published' if summary.published else 'draft'}), "
            f"{len(summary.weeks)} weeks",
        ]

        # Breach weeks are flagged by the engine. The agent reports them; it does not
        # decide which weeks breach by comparing numbers itself.
        for week in summary.weeks:
            if week.breaches_floor:
                lines.append(
                    f"W{week.week_index} ({week.week_ending}) closing "
                    f"{money(week.closing_cash)} breaches the floor"
                )

        for row in drivers.rows:
            state = "STALE" if row.stale else "current"
            lines.append(
                cite(
                    f"driver [{state}] {row.category}: {row.driver} = {row.value_display}, "
                    f"refreshed {row.last_refreshed} ({row.days_since_refresh}d ago)",
                    row.reference,
                )
            )

        if drivers.truncation:
            lines.append(f"drivers {drivers.truncation.display()}")
        return lines
