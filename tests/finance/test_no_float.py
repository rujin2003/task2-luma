from __future__ import annotations

from pathlib import Path

from scripts.check_no_float import scan

FINANCE = Path(__file__).resolve().parents[2] / "backend" / "finance"


def test_no_float_in_finance_package() -> None:
    hits: list[str] = []
    for file in FINANCE.rglob("*.py"):
        hits.extend(scan(file))
    assert hits == []
