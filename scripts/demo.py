"""One-command demo: seed spine + Monday cycle war room golden path.

Person 1 spine (seed → shock → liquidity numbers) plus Person 2 escalation
(cycle → investigation → stress fail → replan → recommendation).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from backend.agents.fake import FakeProvider
from backend.agents.routing import load_routing
from backend.models import Company, ForecastVersion
from backend.orchestrator.bus import EventBus
from backend.orchestrator.weekly_cycle import run_monday_cycle
from backend.seed.generator import SeedConfig, seed
from backend.seed.shocks import SHOCKS, apply
from backend.tools.engine import EngineToolset
from backend.tools.fixtures import FixtureToolset


def _engine(url: str):
    engine = create_engine(url)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _fk(connection, _record):  # type: ignore[no-untyped-def]
            connection.execute("PRAGMA foreign_keys=ON")

        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
    return engine


async def run_spine(*, seed_value: int, db_url: str, shock: bool) -> dict[str, object]:
    engine = _engine(db_url)
    try:
        with Session(engine) as session:
            result = seed(session, SeedConfig(seed=seed_value))
            if shock:
                for name in sorted(SHOCKS):
                    apply(session, name, result.company_id)
            session.commit()

            tools = EngineToolset(session, tenant_id=result.tenant_id)
            liquidity = await tools.get_liquidity_position()
            forecast = await tools.get_forecast_summary()
            covenants = await tools.get_covenant_status()
            company = session.get(Company, result.company_id)
            versions = session.exec(
                select(ForecastVersion).where(ForecastVersion.company_id == result.company_id)
            ).all()
            return {
                "seed": seed_value,
                "company_id": result.company_id,
                "company": None if company is None else company.name,
                "forecast_versions": len(versions),
                "shock_applied": shock,
                "cash_today_minor": liquidity.cash_today.minor_units,
                "min_cash_minor": liquidity.min_cash.minor_units,
                "min_cash_week": liquidity.min_cash_week,
                "floor_minor": liquidity.floor.minor_units,
                "breaches_floor": liquidity.breaches_floor,
                "covenant_breaches": [row.label for row in covenants.covenants if row.breached],
                "forecast_weeks": len(forecast.weeks),
            }
    finally:
        engine.dispose()


async def run_war_room() -> dict[str, object]:
    """Deterministic agentic path against FixtureToolset + FakeProvider."""
    bus = EventBus()
    result = await run_monday_cycle(
        tools=FixtureToolset(),
        bus=bus,
        provider=FakeProvider(strict=True),
        routing=load_routing(),
    )
    inv = result.investigation
    assert inv is not None and inv.recommendation is not None
    rec = inv.recommendation
    return {
        "breached": result.breach is not None,
        "investigation_id": inv.investigation_id,
        "plan_id": inv.plan_id,
        "finding_agents": sorted(f.agent.value for f in inv.findings),
        "replan_count": len(inv.replan_history),
        "selected_strategy_id": rec.selected_strategy.strategy_id,
        "selected_strategy_name": rec.selected_strategy.name,
        "rejected_documents": sorted(
            a.action.document_ref for a in rec.rejected_actions if a.action.document_ref is not None
        ),
        "worklist_rows": len(rec.worklist),
        "approvals": len(inv.approvals),
        "event_count": bus.seq,
        "recommendation_id": rec.recommendation_id,
    }


async def run_demo(*, seed_value: int, db_url: str, shock: bool) -> dict[str, object]:
    """Person 1 spine only — kept for golden-file byte stability in tests."""
    return await run_spine(seed_value=seed_value, db_url=db_url, shock=shock)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAR ROOM golden-path demo")
    parser.add_argument("--seed", type=int, default=int(os.environ.get("SEED", "42")))
    parser.add_argument(
        "--db",
        default=os.environ.get("DATABASE_URL", "sqlite:///warroom-demo.db"),
    )
    parser.add_argument("--no-shock", action="store_true")
    parser.add_argument(
        "--spine-only",
        action="store_true",
        help="Only run the Person 1 seed/shock spine (legacy golden file)",
    )
    parser.add_argument(
        "--golden",
        type=Path,
        default=None,
        help="Write or compare against a golden JSON file (spine numbers)",
    )
    parser.add_argument(
        "--update-golden",
        action="store_true",
        help="Rewrite the golden file instead of comparing",
    )
    args = parser.parse_args(argv)

    spine = asyncio.run(run_spine(seed_value=args.seed, db_url=args.db, shock=not args.no_shock))
    narrative: dict[str, object] = {"spine": spine}
    if not args.spine_only:
        narrative["war_room"] = asyncio.run(run_war_room())

    # Legacy golden file compares the spine block alone when --spine-only, else full narrative
    # stays printable; golden comparison remains on the Person 1 spine for byte-stability.
    rendered_spine = json.dumps(spine, indent=2, sort_keys=True) + "\n"
    rendered_full = json.dumps(narrative, indent=2, sort_keys=True) + "\n"
    sys.stdout.write(rendered_full)

    if args.golden is not None:
        payload = rendered_spine
        if args.update_golden or not args.golden.exists():
            args.golden.parent.mkdir(parents=True, exist_ok=True)
            args.golden.write_text(payload, encoding="utf-8")
        else:
            expected = args.golden.read_text(encoding="utf-8")
            if expected != payload:
                sys.stderr.write("demo spine output drifted from golden file\n")
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
