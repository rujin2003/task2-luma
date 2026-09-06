"""Replayable seed anomalies restore their exact before-images."""

from __future__ import annotations

import pytest
from sqlmodel import Session

from backend.seed.generator import seed
from backend.seed.shocks import SHOCKS


@pytest.mark.parametrize("name", sorted(SHOCKS))
def test_shock_applies_and_rolls_back(session: Session, name: str) -> None:
    result = seed(session)
    token = SHOCKS[name](session, result.company_id)
    assert token.changes, f"{name} did not affect a row"
    changed = [
        (
            change.model,
            change.row_id,
            change.field,
            getattr(session.get(change.model, change.row_id), change.field),
        )
        for change in token.changes
    ]
    assert any(
        value != change.before for (*_, value), change in zip(changed, token.changes, strict=True)
    )

    token.rollback(session)
    for change in token.changes:
        row = session.get(change.model, change.row_id)
        assert row is not None
        assert getattr(row, change.field) == change.before
