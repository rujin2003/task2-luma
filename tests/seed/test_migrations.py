"""Migrations are the schema of record, so they must not drift from the models.

A model change that never reaches a migration is invisible until a deploy, and
by then the seeded database and the declared one disagree. This compares a
freshly migrated database against `SQLModel.metadata` and fails on any
difference.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from backend.models import metadata

ROOT = Path(__file__).resolve().parents[2]

# Column-level differences alembic reports on SQLite that are not real drift:
# SQLite has no native types for these, so a round-trip cannot be compared.
_IGNORED_DIFF_KINDS = frozenset({"modify_type", "modify_nullable"})


def _alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _flatten(diff: object) -> list[tuple[object, ...]]:
    """Alembic returns either a directive tuple or a list of them."""
    if isinstance(diff, list):
        return [item for entry in diff for item in _flatten(entry)]
    return [diff]  # type: ignore[list-item]


def test_migrations_match_the_models(tmp_path: Path) -> None:
    from alembic import command

    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    command.upgrade(_alembic_config(url), "head")

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            diffs = _flatten(compare_metadata(context, metadata))
    finally:
        engine.dispose()

    real = [d for d in diffs if not (d and d[0] in _IGNORED_DIFF_KINDS)]
    assert real == [], f"models and migrations have drifted: {real}"


def test_migration_history_is_linear() -> None:
    script = ScriptDirectory.from_config(_alembic_config("sqlite://"))
    heads = script.get_heads()
    assert len(heads) == 1, f"expected a single head, found {heads}"


@pytest.mark.parametrize("_", [None])
def test_rls_migration_covers_every_tenant_table(_: None) -> None:
    """A table with `tenant_id` and no policy is an isolation hole."""
    module = ROOT / "alembic" / "versions" / "20260906_0002_row_level_security.py"
    namespace: dict[str, object] = {}
    source = module.read_text(encoding="utf-8")
    # Execute only the constant block; the migration body needs an alembic context.
    start = source.index("TENANT_TABLES")
    end = source.index("def _is_postgres")
    exec(compile(source[start:end], str(module), "exec"), namespace)
    covered = set(namespace["TENANT_TABLES"])  # type: ignore[arg-type]

    expected = {name for name, table in metadata.tables.items() if "tenant_id" in table.columns}
    assert expected - covered == set(), (
        f"tables without an RLS policy: {sorted(expected - covered)}"
    )
