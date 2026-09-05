"""The frontend's hand-written mirror must not drift from the Python event schema.

Cheaper than code generation and it fails in the same CI run, which is the point: a new
event type that only exists on one side is caught the day it is added, not the day the
UI silently stops rendering it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.contracts import EVENT_SCHEMA_VERSION, EventType

EVENTS_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "events.ts"


@pytest.fixture(scope="module")
def events_ts() -> str:
    if not EVENTS_TS.exists():  # pragma: no cover - only before the frontend lands
        pytest.skip("frontend/src/lib/events.ts is not present")
    return EVENTS_TS.read_text(encoding="utf-8")


def test_the_typescript_vocabulary_matches_the_python_enum(events_ts: str) -> None:
    block = re.search(r"export const EVENT_TYPES = \[(.*?)\] as const;", events_ts, re.S)
    assert block, "EVENT_TYPES literal not found in events.ts"

    declared = set(re.findall(r'"([^"]+)"', block.group(1)))
    assert declared == {t.value for t in EventType}


def test_the_schema_version_matches(events_ts: str) -> None:
    declared = re.search(r'EVENT_SCHEMA_VERSION = "([^"]+)"', events_ts)
    assert declared and declared.group(1) == EVENT_SCHEMA_VERSION
