"""Command-line entry point for the full NovaTech seed."""

from __future__ import annotations

import os

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from backend.seed.generator import SeedConfig, seed


def main() -> None:
    url = os.environ.get("DATABASE_URL", "sqlite:///novatech.db")
    seed_value = int(os.environ.get("SEED", "42"))
    engine = create_engine(url)
    if url.startswith("sqlite"):
        event.listen(
            engine,
            "connect",
            lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"),
        )
        SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            result = seed(session, SeedConfig(seed=seed_value))
            session.commit()
        counts = ", ".join(f"{table}={count}" for table, count in result.row_counts.items())
        print(f"seeded {result.company_id} into {url} (SEED={seed_value})")
        print(counts)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
