"""The War Room the screen shows must be the War Room the orchestrator ran.

`frontend/src/fixtures/*.json` is recorded from the golden path by
`scripts/export_frontend_fixtures.py`. That gives the screens real event ordering, a real
conflict, a real replan and a real refusal -- and it only stays true if regenerating is a
no-op. So this re-runs the recorder and compares bytes.

When it fails, the fix is almost always to run the script and read the diff:

    python -m scripts.export_frontend_fixtures

The diff is the interesting part. A change in the recommendation's summary means the
composition changed; a change in the event ordering means the state machine did. Either
may be correct, but neither should reach a screen without somebody having seen it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.export_frontend_fixtures import _stable, record

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "frontend" / "src" / "fixtures"
NAMES = ("war-room", "recommendation", "approvals", "cycle")


@pytest.fixture(scope="module")
async def recorded() -> dict[str, str]:
    return {name: _stable(payload) for name, payload in (await record()).items()}


@pytest.mark.parametrize("name", NAMES)
async def test_the_committed_fixture_is_what_the_golden_path_produces(recorded, name) -> None:
    path = FIXTURES / f"{name}.json"
    if not path.exists():  # pragma: no cover -- only before the frontend lands
        pytest.skip(f"{path.name} is not present")

    assert path.read_text(encoding="utf-8") == recorded[name], (
        f"{path.name} is stale; run `python -m scripts.export_frontend_fixtures`"
    )


async def test_recording_twice_produces_the_same_bytes() -> None:
    """Determinism is the property the whole demo rests on, so it is asserted directly."""
    first = {name: _stable(payload) for name, payload in (await record()).items()}
    second = {name: _stable(payload) for name, payload in (await record()).items()}

    assert first == second


# --- the rules the screens are built on ------------------------------------------------


@pytest.fixture(scope="module")
def war_room() -> dict:
    path = FIXTURES / "war-room.json"
    if not path.exists():  # pragma: no cover
        pytest.skip("war-room.json is not present")
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_recorded_stream_carries_no_chain_of_thought(war_room) -> None:
    """No field for a model's reasoning anywhere in the schema, and none in the bundle."""
    banned = {"reasoning", "thought", "thinking", "chain_of_thought", "scratchpad", "prompt"}

    for event in war_room["events"]:
        assert not banned & set(event), event["type"]
    for run in war_room["runs"]:
        assert not banned & set(run)
        assert "context_pack" not in run, "the brief is a prompt; it does not ship to a browser"


def test_the_recording_includes_the_parts_nobody_would_hand_write(war_room) -> None:
    """A tidy fixture is the failure mode; these are the rows that make the screen honest."""
    types = {event["type"] for event in war_room["events"]}

    assert "conflict.detected" in types, "two agents disagreeing is the point of the screen"
    assert "conflict.resolved" in types
    assert "replan.started" in types, "a bundle failed stress and was replanned"
    assert "plan.selected" in types


def test_every_event_can_be_rendered_from_the_envelope_alone(war_room) -> None:
    """A client that understands nothing else still draws the feed correctly."""
    for event in war_room["events"]:
        assert event["status_line"]
        assert event["mark"] in {"ok", "warn", "working", "fail", "info"}
        assert isinstance(event["seq"], int)


def test_the_stream_is_a_gapless_sequence(war_room) -> None:
    """`seq` is the resume cursor, so a hole in it is a client that silently misses rows."""
    seqs = [event["seq"] for event in war_room["events"]]

    assert seqs == list(range(len(seqs)))
