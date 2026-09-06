"""Dodo decline classification with explicitly supplied recovery calibration."""

from __future__ import annotations

from enum import StrEnum


class DeclineKind(StrEnum):
    SOFT = "soft"
    HARD = "hard"


SOFT_DECLINE_CODES = frozenset(
    {
        "authentication_required",
        "do_not_honor",
        "insufficient_funds",
        "processing_error",
        "temporary_hold",
    }
)

HARD_DECLINE_CODES = frozenset(
    {
        "card_permanently_declined",
        "fraudulent",
        "invalid_account",
        "lost_or_stolen_card",
        "restricted_card",
    }
)


def classify_decline(code: str) -> DeclineKind:
    normalized = code.strip().lower()
    if normalized in SOFT_DECLINE_CODES:
        return DeclineKind.SOFT
    if normalized in HARD_DECLINE_CODES:
        return DeclineKind.HARD
    raise ValueError(f"unknown Dodo decline code {code!r}")


def recovery_bps(code: str, *, soft_recovery_bps: int) -> int:
    """Apply the documented profile rate only to retryable soft declines."""
    if not 0 <= soft_recovery_bps <= 10_000:
        raise ValueError("soft_recovery_bps must be between 0 and 10000")
    return soft_recovery_bps if classify_decline(code) is DeclineKind.SOFT else 0


__all__ = [
    "HARD_DECLINE_CODES",
    "SOFT_DECLINE_CODES",
    "DeclineKind",
    "classify_decline",
    "recovery_bps",
]
