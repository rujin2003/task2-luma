"""Exit tests for the complete deterministic NovaTech dataset."""

from __future__ import annotations

from sqlalchemy import Engine, text
from sqlmodel import Session

from backend.models import ForecastVersion, metadata
from backend.models.invariants import assert_consistent
from backend.seed.generator import SeedConfig, seed


def _dump(engine: Engine) -> list[tuple[str, tuple[object, ...]]]:
    dumped: list[tuple[str, tuple[object, ...]]] = []
    with Session(engine) as session:
        for name in sorted(metadata.tables):
            columns = ", ".join(sorted(metadata.tables[name].columns.keys()))
            rows = session.execute(text(f"SELECT {columns} FROM {name} ORDER BY id")).fetchall()
            dumped.extend((name, tuple(row)) for row in rows)
    return dumped


def test_novatech_seed_is_deterministic_and_complete(make_engine) -> None:  # type: ignore[no-untyped-def]
    first, second = make_engine(), make_engine()
    results = []
    for engine in (first, second):
        with Session(engine) as session:
            result = seed(session, SeedConfig(seed=42))
            assert_consistent(session)
            session.commit()
            results.append(result)

    assert _dump(first) == _dump(second)
    assert results[0].weekly_actuals
    assert results[0].weekly_actuals == results[1].weekly_actuals
    with Session(first) as session:
        count = session.execute(
            text(f"SELECT COUNT(*) FROM {ForecastVersion.__tablename__}")
        ).scalar()
    assert count is not None and count >= 26
