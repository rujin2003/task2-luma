"""The Evidence Explorer must show the ledger, not a stale copy of it.

`frontend/src/fixtures/evidence.ts` is generated from the same JSON the `resolve_evidence`
tool reads. If someone adds a row on one side only, the provenance chain the analyst walks
stops agreeing with the rows the agents cite -- which is exactly the failure the Evidence
Explorer exists to make impossible. So parity is a build failure, not a convention.

    npm --prefix frontend run gen:evidence
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tests" / "fixtures" / "tools" / "resolve_evidence.json"
GENERATED = ROOT / "frontend" / "src" / "fixtures" / "evidence.ts"
FORECAST_FIXTURE = ROOT / "frontend" / "src" / "fixtures" / "forecast.ts"


@pytest.fixture(scope="module")
def generated() -> list[dict[str, object]]:
    if not GENERATED.exists():  # pragma: no cover -- only before the frontend lands
        pytest.skip("frontend/src/fixtures/evidence.ts is not present")
    block = re.search(
        r"export const EVIDENCE_ROWS: EvidenceRecord\[\] = (\[.*\]);",
        GENERATED.read_text(encoding="utf-8"),
        re.S,
    )
    assert block, "EVIDENCE_ROWS literal not found in evidence.ts"
    rows: list[dict[str, object]] = json.loads(block.group(1))
    return rows


@pytest.fixture(scope="module")
def source() -> dict[str, dict[str, object]]:
    return json.loads(SOURCE.read_text(encoding="utf-8"))


def test_every_resolvable_row_reached_the_frontend(generated, source) -> None:
    assert {row["reference"] for row in generated} == set(source)


def test_the_excerpt_shown_is_the_excerpt_stored(generated, source) -> None:
    """The whole point of the chain is that the screen and the row say the same thing."""
    for row in generated:
        assert row["excerpt"] == source[str(row["reference"])]["excerpt"]


def test_the_provenance_chain_only_points_at_rows_that_exist(generated) -> None:
    references = {row["reference"] for row in generated}
    for row in generated:
        derived = row["derived_from"]
        assert isinstance(derived, list)
        dangling = [parent for parent in derived if parent not in references]
        assert not dangling, f"{row['reference']} derives from missing rows: {dangling}"


def test_at_least_one_aggregate_can_be_walked_down(generated) -> None:
    """A chain of length one is a citation; the Explorer is for the ones that go deeper."""
    assert any(row["derived_from"] for row in generated)


# --- the Forecast screen's own promise -------------------------------------------------


@pytest.fixture(scope="module")
def forecast_ts() -> str:
    if not FORECAST_FIXTURE.exists():  # pragma: no cover -- only before the frontend lands
        pytest.skip("frontend/src/fixtures/forecast.ts is not present")
    return FORECAST_FIXTURE.read_text(encoding="utf-8")


def test_every_forecast_row_declares_a_source(forecast_ts: str) -> None:
    """Phase 9's acceptance test: every number on the screen traces to a source row.

    A row without a reference renders numbers nobody can pull on, which is exactly the
    kind of figure that ends up in a board pack with no way back to the ledger.
    """
    block = re.search(r"const ROWS: ForecastRow\[\] = \[(.*?)\n\];", forecast_ts, re.S)
    assert block, "ROWS literal not found in forecast.ts"

    chunks = [chunk for chunk in block.group(1).split("\n  row(") if chunk.strip()]
    unsourced = [
        chunk.strip().splitlines()[0] for chunk in chunks if "reference:" not in chunk
    ]
    assert not unsourced, f"forecast rows with no source row: {unsourced}"


def test_every_reference_the_screen_shows_resolves(forecast_ts: str, source) -> None:
    used = set(re.findall(r'reference: "([^"]+)"', forecast_ts))
    used |= set(re.findall(r'_reference: "([^"]+)"', forecast_ts))
    assert used, "the forecast fixture cites nothing at all"
    assert used <= set(source), f"unresolvable on screen: {sorted(used - set(source))}"
