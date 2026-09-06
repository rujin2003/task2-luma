"""Same seed, same bytes.

Phase 2's exit criteria is `make seed SEED=42` twice giving identical dumps.
That is unattainable with random primary keys, and the failure would surface a
phase later as an unexplained diff, so the guarantee is tested here — at the
point the identifiers are chosen — rather than after the generator exists.
"""

from __future__ import annotations

from sqlalchemy import Engine, text
from sqlmodel import Session

from backend.models import derived_id, metadata
from backend.seed.trivial import seed_trivial


def _dump(engine: Engine) -> list[tuple[str, tuple[object, ...]]]:
    rows: list[tuple[str, tuple[object, ...]]] = []
    with Session(engine) as session:
        for name in sorted(metadata.tables):
            columns = ", ".join(sorted(metadata.tables[name].columns.keys()))
            statement = text(f"SELECT {columns} FROM {name} ORDER BY id")
            rows.extend((name, tuple(row)) for row in session.execute(statement).fetchall())
    return rows


def test_two_runs_produce_identical_dumps(make_engine) -> None:  # type: ignore[no-untyped-def]
    first, second = make_engine(), make_engine()
    for engine in (first, second):
        with Session(engine) as session:
            seed_trivial(session)

    dump = _dump(first)
    assert dump, "seed produced no rows"
    assert dump == _dump(second)


def test_derived_ids_are_stable_and_distinct() -> None:
    assert derived_id("invoices", "co", "INV-1001") == derived_id("invoices", "co", "INV-1001")
    assert derived_id("invoices", "co", "INV-1001") != derived_id("invoices", "co", "INV-1002")
    # The kind prefix keeps natural keys from colliding across tables.
    assert derived_id("invoices", "X") != derived_id("payments", "X")


def test_primary_keys_are_reproducible_across_runs(make_engine) -> None:  # type: ignore[no-untyped-def]
    def invoice_ids(engine: Engine) -> list[str]:
        with Session(engine) as session:
            seed_trivial(session)
            return [
                row[0]
                for row in session.execute(
                    text("SELECT id FROM invoices ORDER BY invoice_ref")
                ).fetchall()
            ]

    assert invoice_ids(make_engine()) == invoice_ids(make_engine())
