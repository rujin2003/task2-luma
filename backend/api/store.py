"""The target database: where a tenant's loaded rows actually land.

The rest of the API is deliberately in-memory (see `session.py`), and that is fine for
a forecast version or an approval card. It is not fine for the rows the DB Agent reads
out of a customer's system: those are the tenant's ledger, they are large, and losing
them on a process restart would mean asking the POC to re-run the load to see the same
screen twice.

One engine per process, created lazily so importing the API does not touch the disk,
and `create_all` rather than Alembic because the target here is a demo tenant store
that is expected to be thrown away and rebuilt. A production tenant store is a
migration target; this is not pretending otherwise.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine

import backend.models  # noqa: F401  -- import for the side effect of registering metadata

DEFAULT_URL = "sqlite:///var/warroom-tenant.db"

_engine: Engine | None = None


def target_url() -> str:
    return os.environ.get("WARROOM_TENANT_DB", DEFAULT_URL)


def engine() -> Engine:
    global _engine
    if _engine is not None:
        return _engine
    url = target_url()
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    created = create_engine(url)
    if created.url.get_backend_name() == "sqlite":
        # SQLite leaves foreign keys off per connection. The model layer relies on them
        # to make an unparented invoice unrepresentable, so this is not optional.
        event.listen(
            created,
            "connect",
            lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"),
        )
    SQLModel.metadata.create_all(created)
    _engine = created
    return created


@contextmanager
def session() -> Iterator[Session]:
    """A unit of work. The caller commits; an exception rolls the whole load back."""
    with Session(engine()) as opened:
        yield opened


def reset() -> None:
    """Drop the process engine. Used by tests that repoint `WARROOM_TENANT_DB`."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


__all__ = ["DEFAULT_URL", "engine", "reset", "session", "target_url"]
