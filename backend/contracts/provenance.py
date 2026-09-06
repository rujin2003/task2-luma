"""Provenance and evidence.

Nothing enters the system without a `Provenance`. That is what makes "never fabricate
evidence" a type-system property rather than a line in a prompt: an agent cannot emit a
finding without references, and the evidence validator rejects references that do not
resolve to a real row.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class SourceSystem(StrEnum):
    """Where a fact came from. Extend deliberately; agents cite these by name."""

    BANK = "bank"
    GL = "gl"
    AR_LEDGER = "ar_ledger"
    AP_LEDGER = "ap_ledger"
    PAYROLL = "payroll"
    TAX_CALENDAR = "tax_calendar"
    DEBT = "debt"
    DODO = "dodo"
    FORECAST = "forecast"
    POLICY = "policy"
    OVERRIDE = "override"


NonEmptyStr = Annotated[str, Field(min_length=1)]


class Provenance(BaseModel):
    """`(source_system, record_id, field, as_of, retrieved_at)` -- the whole point.

    `as_of` is when the fact was true; `retrieved_at` is when we learned it. Keeping
    both is what lets the Evidence Explorer answer "what did we know when we made this
    recommendation".
    """

    model_config = ConfigDict(frozen=True)

    source_system: SourceSystem
    record_id: NonEmptyStr
    field: str | None = None
    as_of: datetime
    retrieved_at: datetime
    is_synthetic: bool = True

    @property
    def reference(self) -> str:
        """The stable citation string an agent puts in `evidence[].reference`."""
        base = f"{self.source_system.value}:{self.record_id}"
        return f"{base}#{self.field}" if self.field else base


class Evidence(BaseModel):
    """A citation attached to a finding, plus the human-readable claim it supports."""

    model_config = ConfigDict(frozen=True)

    reference: NonEmptyStr
    source: SourceSystem
    excerpt: Annotated[str, Field(max_length=280)]
    provenance: Provenance | None = None

    @classmethod
    def from_provenance(cls, provenance: Provenance, excerpt: str) -> Evidence:
        return cls(
            reference=provenance.reference,
            source=provenance.source_system,
            excerpt=excerpt,
            provenance=provenance,
        )


def parse_reference(reference: str) -> tuple[str, str, str | None]:
    """Split `source:record_id#field` back into its parts.

    The evidence validator uses this to look the row up; a reference that does not
    parse is already a rejection.
    """
    if ":" not in reference:
        raise ValueError(f"malformed evidence reference {reference!r}: expected 'source:record_id'")
    source, rest = reference.split(":", 1)
    field: str | None = None
    if "#" in rest:
        rest, field = rest.split("#", 1)
    if not source or not rest:
        raise ValueError(f"malformed evidence reference {reference!r}")
    return source, rest, field
