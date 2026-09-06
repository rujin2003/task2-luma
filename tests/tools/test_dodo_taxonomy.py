from __future__ import annotations

import pytest

from backend.integrations.dodo.taxonomy import (
    DeclineKind,
    classify_decline,
    recovery_bps,
)


def test_soft_and_hard_declines_are_distinct() -> None:
    assert classify_decline("insufficient_funds") is DeclineKind.SOFT
    assert classify_decline("lost_or_stolen_card") is DeclineKind.HARD
    assert recovery_bps("insufficient_funds", soft_recovery_bps=7200) == 7200
    assert recovery_bps("lost_or_stolen_card", soft_recovery_bps=7200) == 0


def test_unknown_decline_is_not_guessed() -> None:
    with pytest.raises(ValueError, match="unknown Dodo decline"):
        classify_decline("new_provider_code")
