"""One-command demo: reset → seed → shock → print deterministic numbers.

Person 1 half of Phase 11. Same SEED produces identical output every run.
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

from backend.models import Company, ForecastVersion
from backend.seed.generator import SeedConfig, seed
from backend.seed.shocks import SHOCKS, apply
from backend.tools.engine import EngineToolset


def _engine(url: str):
    engine = create_engine(url)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _fk(connection, _record):  # type: ignore[no-untyped-def]
            connection.execute("PRAGMA foreign_keys=ON")

        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
    return engine


async def run_demo(*, seed_value: int, db_url: str, shock: bool) -> dict[str, object]:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAR ROOM golden-path demo (Person 1 spine)")
    parser.add_argument("--seed", type=int, default=int(os.environ.get("SEED", "42")))
    parser.add_argument(
        "--db",
        default=os.environ.get("DATABASE_URL", "sqlite:///warroom-demo.db"),
    )
    parser.add_argument("--no-shock", action="store_true")
    parser.add_argument(
        "--golden",
        type=Path,
        default=None,
        help="Write or compare against a golden JSON file",
    )
    parser.add_argument(
        "--update-golden",
        action="store_true",
        help="Rewrite the golden file instead of comparing",
    )
    args = parser.parse_args(argv)
    narrative = asyncio.run(run_demo(seed_value=args.seed, db_url=args.db, shock=not args.no_shock))
    rendered = json.dumps(narrative, indent=2, sort_keys=True) + "\n"
    sys.stdout.write(rendered)

    if args.golden is not None:
        if args.update_golden or not args.golden.exists():
            args.golden.parent.mkdir(parents=True, exist_ok=True)
            args.golden.write_text(rendered, encoding="utf-8")
        else:
            expected = args.golden.read_text(encoding="utf-8")
            if expected != rendered:
                sys.stderr.write("demo output drifted from golden file\n")
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
