"""Shared pytest fixtures. Tool JSON lives in tests/fixtures/ for Person 2."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_json() -> Any:
    def _load(name: str) -> Any:
        path = FIXTURES / name
        return json.loads(path.read_text(encoding="utf-8"))

    return _load
