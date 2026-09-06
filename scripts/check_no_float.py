#!/usr/bin/env python3
"""Fail CI if `float` appears in the financial path.

Scans backend/finance/**, backend/models/** and backend/seed/** — the model and
seed layers define and populate the money columns, so a float there is the same
defect one layer down.

Looks for:
  - the `float` builtin used as a call or annotation
  - `from ... import float` (impossible but caught)
  - float literals (e.g. 1.5) — amounts and rates must be ints or digit strings

Comments and docstrings may mention the word "float".
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNED = ("backend/finance", "backend/models", "backend/seed")


class FloatVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.hits: list[str] = []

    def _mark(self, node: ast.AST, message: str) -> None:
        self.hits.append(f"{self.path}:{node.lineno}:{node.col_offset}: {message}")

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == "float":
            self._mark(node, "use of name `float` is forbidden in backend/finance")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if type(node.value) is float:
            self._mark(node, f"float literal {node.value!r} is forbidden here")
        self.generic_visit(node)


def scan(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    visitor = FloatVisitor(path.relative_to(ROOT))
    visitor.visit(tree)
    return visitor.hits


def main() -> int:
    hits: list[str] = []
    scanned = 0
    for relative in SCANNED:
        directory = ROOT / relative
        if not directory.is_dir():
            print(f"missing {directory}", file=sys.stderr)
            return 2
        for file in sorted(directory.rglob("*.py")):
            scanned += 1
            hits.extend(scan(file))
    if hits:
        print(f"float is forbidden in {', '.join(f'{d}/**' for d in SCANNED)}:", file=sys.stderr)
        for hit in hits:
            print(f"  {hit}", file=sys.stderr)
        return 1
    print(f"ok: no float in {scanned} modules across {len(SCANNED)} packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
