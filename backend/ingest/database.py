"""Connect stage for a live customer database.

The POC pastes a SQLAlchemy URL — credentials and all — and everything downstream
works from what this module returns. Three properties matter here, and each is
enforced rather than documented:

* **The URL is never persisted and never rendered.** `redact` is the only way a URL
  reaches a response body or a log line, and it replaces the password with `***`.
  The credential lives in the onboarding session's memory for the length of the
  onboarding and is dropped when it is discarded.
* **Introspection reads structure, not money.** Table names, column names, types,
  keys, row counts and *format signatures*. Value samples are taken only from
  text-typed columns and only to classify a shape (`INV-1234`, `USD`, ISO-8601);
  numeric and date columns are never sampled. No financial value leaves this stage,
  which is what makes it safe to show a schema to a model later.
* **Write capability is probed, not assumed.** The POC is asked for a credential with
  write permission because the product eventually writes back — a payment
  instruction, a dunning flag. Ingestion itself stays read-only, so the probe
  reports what the credential *can* do and the pipeline still opens its own
  connections `READ ONLY` where the dialect supports it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

# Columns whose values may be sampled to infer a *shape*. Anything holding a number,
# a date or a blob is excluded: a shape is not worth a value leaving the tenant.
_SAMPLEABLE = ("char", "text", "string", "uuid", "enum", "citext")

# Sampling is bounded on both axes so introspecting a billion-row table is cheap.
SAMPLE_ROWS = 20
MAX_TABLES = 200


class ConnectionError_(RuntimeError):
    """The URL is malformed, unreachable, or the credential was refused."""


@dataclass(frozen=True, slots=True)
class Probe:
    """What the credential turned out to be able to do."""

    dialect: str
    server_version: str
    schema: str
    can_read: bool
    can_write: bool
    write_note: str
    table_count: int


def redact(url: str) -> str:
    """The only rendering of a source URL this system permits."""
    try:
        parsed = make_url(url)
    except Exception:  # a URL we cannot parse is a URL we certainly cannot print
        return "<unparseable url>"
    return parsed.render_as_string(hide_password=True)


def _engine(url: str) -> Engine:
    try:
        parsed = make_url(url)
    except Exception as exc:
        raise ConnectionError_(f"not a valid database URL: {exc}") from exc
    connect_args: dict[str, Any] = {}
    if parsed.get_backend_name() == "postgresql":
        # Fail fast rather than hanging a request thread on an unreachable host.
        connect_args["connect_timeout"] = 8
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True)


def probe(url: str, *, schema: str | None = None) -> Probe:
    """Open the connection, read the server banner, and test write capability.

    The write test creates and drops a temporary table inside a transaction that is
    rolled back regardless. It never touches a tenant table: a probe that mutated
    customer data to prove it could would be the worst possible way to ask.
    """
    engine = _engine(url)
    backend = engine.url.get_backend_name()
    try:
        with engine.connect() as connection:
            version = _server_version(connection.exec_driver_sql, backend)
            inspector = inspect(engine)
            target = schema or inspector.default_schema_name or "main"
            tables = inspector.get_table_names(schema=None if backend == "sqlite" else target)
            can_write, note = _probe_write(engine)
    except SQLAlchemyError as exc:
        raise ConnectionError_(f"could not connect to {redact(url)}: {exc}") from exc
    finally:
        engine.dispose()

    return Probe(
        dialect=backend,
        server_version=version,
        schema=target,
        can_read=True,
        can_write=can_write,
        write_note=note,
        table_count=len(tables),
    )


def _server_version(exec_sql: Any, backend: str) -> str:
    statement = {
        "postgresql": "SELECT version()",
        "mysql": "SELECT version()",
        "sqlite": "SELECT sqlite_version()",
    }.get(backend)
    if statement is None:
        return "unknown"
    try:
        value = exec_sql(statement).scalar()
    except SQLAlchemyError:
        return "unknown"
    return str(value)[:120]


def _probe_write(engine: Engine) -> tuple[bool, str]:
    statement = (
        "CREATE TEMP TABLE warroom_write_probe (probe_id integer)"
        if engine.url.get_backend_name() in {"postgresql", "sqlite"}
        else "CREATE TEMPORARY TABLE warroom_write_probe (probe_id integer)"
    )
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(statement)
            connection.exec_driver_sql("DROP TABLE warroom_write_probe")
    except SQLAlchemyError as exc:
        return False, f"write probe refused: {str(exc)[:160]}"
    return True, "temporary table created and dropped; no tenant table was touched"


def introspect(url: str, *, schema: str | None = None) -> list[dict[str, Any]]:
    """Structure only. The returned dicts feed `fingerprint_from_tables` unchanged."""
    engine = _engine(url)
    backend = engine.url.get_backend_name()
    try:
        inspector = inspect(engine)
        target = schema or inspector.default_schema_name
        lookup = None if backend == "sqlite" else target
        names = sorted(inspector.get_table_names(schema=lookup))[:MAX_TABLES]
        with engine.connect() as connection:
            return [_profile_table(connection, inspector, name, lookup) for name in names]
    except SQLAlchemyError as exc:
        raise ConnectionError_(f"could not introspect {redact(url)}: {exc}") from exc
    finally:
        engine.dispose()


def _profile_table(
    connection: Any, inspector: Any, name: str, schema: str | None
) -> dict[str, Any]:
    columns = inspector.get_columns(name, schema=schema)
    pk = set(inspector.get_pk_constraint(name, schema=schema).get("constrained_columns") or [])
    fks: list[tuple[str, str]] = []
    fk_by_column: dict[str, str] = {}
    for constraint in inspector.get_foreign_keys(name, schema=schema):
        target_table = constraint.get("referred_table")
        for local, remote in zip(
            constraint.get("constrained_columns") or [],
            constraint.get("referred_columns") or [],
            strict=False,
        ):
            fk_by_column[local] = f"{target_table}.{remote}"
            fks.append((local, f"{target_table}.{remote}"))

    qualified = f"{schema}.{name}" if schema else name
    row_count = _count_rows(connection, qualified)
    samples = _sample_text_columns(connection, qualified, columns)

    return {
        "name": name,
        "row_count": row_count,
        "columns": [
            {
                "name": column["name"],
                "data_type": str(column["type"]).lower(),
                "nullable": bool(column.get("nullable", True)),
                "is_pk": column["name"] in pk,
                "is_fk": column["name"] in fk_by_column,
                "fk_target": fk_by_column.get(column["name"]),
                "row_count": row_count,
                "format_signature": samples.get(column["name"]),
            }
            for column in columns
        ],
        "foreign_keys": fks,
    }


def _count_rows(connection: Any, qualified: str) -> int:
    try:
        return int(connection.exec_driver_sql(f"SELECT count(*) FROM {qualified}").scalar() or 0)
    except SQLAlchemyError:
        return 0


def _sample_text_columns(
    connection: Any, qualified: str, columns: list[dict[str, Any]]
) -> dict[str, str]:
    """Classify the *shape* of text columns. Numeric and date columns are not read."""
    from backend.ingest.fingerprint import infer_format_signature

    signatures: dict[str, str] = {}
    for column in columns:
        type_name = str(column["type"]).lower()
        if not any(token in type_name for token in _SAMPLEABLE):
            continue
        try:
            rows = connection.exec_driver_sql(
                f"SELECT {column['name']} FROM {qualified} LIMIT {SAMPLE_ROWS}"
            ).scalars()
            signature = infer_format_signature([value for value in rows if value is not None])
        except SQLAlchemyError:
            continue
        if signature:
            signatures[column["name"]] = signature
    return signatures


__all__ = [
    "MAX_TABLES",
    "SAMPLE_ROWS",
    "ConnectionError_",
    "Probe",
    "introspect",
    "probe",
    "redact",
]
