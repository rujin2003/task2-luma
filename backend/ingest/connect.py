"""Connect stage — read-only source handles.

The engine never holds write capability against a customer database.
Supported entry points for v1: Postgres URL (introspected offline), CSV upload.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

SourceKind = Literal["postgres", "csv", "dodo", "stripe_quickbooks"]


@dataclass(frozen=True, slots=True)
class SourceConnection:
    kind: SourceKind
    label: str
    read_only: bool = True


@dataclass(frozen=True, slots=True)
class CsvSource(SourceConnection):
    directory: Path = Path(".")

    def __init__(self, directory: Path, *, label: str = "csv-upload") -> None:
        object.__setattr__(self, "kind", "csv")
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "read_only", True)
        object.__setattr__(self, "directory", directory)


def introspect_csv(directory: Path) -> list[dict[str, Any]]:
    """Build table profiles from CSV headers only — no cell values leave this stage."""
    tables: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            try:
                headers = next(reader)
            except StopIteration:
                headers = []
            row_count = sum(1 for _ in reader)
        columns = [
            {
                "name": name.strip(),
                "data_type": "text",
                "nullable": True,
                "row_count": row_count,
            }
            for name in headers
            if name.strip()
        ]
        tables.append(
            {
                "name": path.stem,
                "row_count": row_count,
                "columns": columns,
                "foreign_keys": [],
            }
        )
    return tables


__all__ = ["CsvSource", "SourceConnection", "SourceKind", "introspect_csv"]
