"""Schema fingerprint — column names, types and shapes only. Never values."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict


class ColumnProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    data_type: str
    nullable: bool = True
    is_pk: bool = False
    is_fk: bool = False
    fk_target: str | None = None
    row_count: int = 0
    null_rate_bps: int = 0
    distinct_count: int = 0
    format_signature: str | None = None


class TableProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    row_count: int = 0
    columns: tuple[ColumnProfile, ...] = ()
    foreign_keys: tuple[tuple[str, str], ...] = ()  # (column, target_table.column)


class SchemaFingerprint(BaseModel):
    """Compact structural digest of a source schema. Contains no financial values."""

    model_config = ConfigDict(frozen=True)

    source_kind: str
    dialect: str = "unknown"
    tables: tuple[TableProfile, ...] = ()
    fingerprint_hash: str = ""

    def with_hash(self) -> SchemaFingerprint:
        payload = self.model_dump(mode="json", exclude={"fingerprint_hash"})
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return self.model_copy(update={"fingerprint_hash": digest})


_INVOICE_RE = re.compile(r"^INV[-_]?\d+$", re.I)
_ISO_CCY = re.compile(r"^[A-Z]{3}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def infer_format_signature(samples: list[Any]) -> str | None:
    """Classify a column's *shape* from non-value samples (strings only)."""
    texts = [str(s) for s in samples if s is not None]
    if not texts:
        return None
    if all(_INVOICE_RE.match(t) for t in texts):
        return r"^INV-\d+$"
    if all(_ISO_CCY.match(t) for t in texts):
        return r"^[A-Z]{3}$"
    if all(_ISO_DATE.match(t) for t in texts):
        return "ISO8601"
    return None


def fingerprint_from_tables(
    tables: list[dict[str, Any]],
    *,
    source_kind: str,
    dialect: str = "postgres",
) -> SchemaFingerprint:
    """Build a fingerprint from introspected table dicts (no row values)."""
    profiles: list[TableProfile] = []
    for table in tables:
        columns = tuple(
            ColumnProfile(
                name=str(col["name"]),
                data_type=str(col.get("data_type", col.get("type", "unknown"))),
                nullable=bool(col.get("nullable", True)),
                is_pk=bool(col.get("is_pk", False)),
                is_fk=bool(col.get("is_fk", False)),
                fk_target=col.get("fk_target"),
                row_count=int(col.get("row_count", table.get("row_count", 0))),
                null_rate_bps=int(col.get("null_rate_bps", 0)),
                distinct_count=int(col.get("distinct_count", 0)),
                format_signature=col.get("format_signature"),
            )
            for col in table.get("columns", [])
        )
        fks = tuple((str(a), str(b)) for a, b in table.get("foreign_keys", []))
        profiles.append(
            TableProfile(
                name=str(table["name"]),
                row_count=int(table.get("row_count", 0)),
                columns=columns,
                foreign_keys=fks,
            )
        )
    return SchemaFingerprint(
        source_kind=source_kind,
        dialect=dialect,
        tables=tuple(sorted(profiles, key=lambda t: t.name)),
    ).with_hash()


__all__ = [
    "ColumnProfile",
    "SchemaFingerprint",
    "TableProfile",
    "fingerprint_from_tables",
    "infer_format_signature",
]
