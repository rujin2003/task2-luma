from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.demo import run_demo

GOLDEN = Path("tests/fixtures/demo/golden_seed_42.json")


@pytest.mark.asyncio
async def test_demo_narrative_matches_golden(tmp_path: Path) -> None:
    db = tmp_path / "demo.db"
    narrative = await run_demo(
        seed_value=42,
        db_url=f"sqlite:///{db}",
        shock=True,
    )
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert narrative == expected


@pytest.mark.asyncio
async def test_demo_is_byte_identical_across_runs(tmp_path: Path) -> None:
    first = await run_demo(
        seed_value=42,
        db_url=f"sqlite:///{tmp_path / 'a.db'}",
        shock=True,
    )
    second = await run_demo(
        seed_value=42,
        db_url=f"sqlite:///{tmp_path / 'b.db'}",
        shock=True,
    )
    assert first == second
