"""Engine fixtures for model-layer tests.

SQLite does not enforce foreign keys unless asked, so the pragma is switched on
here. Without it these tests would silently stop checking referential integrity,
which is most of what Phase 1 claims to deliver.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine


def _make_engine(url: str = "sqlite://") -> Engine:
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def engine() -> Iterator[Engine]:
    created = _make_engine()
    yield created
    created.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as open_session:
        yield open_session


@pytest.fixture
def make_engine():  # type: ignore[no-untyped-def]
    """Factory for tests that need two independent databases."""
    engines: list[Engine] = []

    def _factory() -> Engine:
        created = _make_engine()
        engines.append(created)
        return created

    yield _factory
    for created in engines:
        created.dispose()
