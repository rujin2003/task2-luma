"""Provenance primitive. Nothing enters the system without one."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Provenance(BaseModel):
    """Trace a value back to a source row at a point in time.

    Fields:
        source_system: originating system (erp, bank, dodo, seed, ...)
        source_table: table or collection name in that system
        source_pk: primary key as a string (never a computed number)
        field: column or field that produced the value
        as_of: the business time of the fact (effective time)
        retrieved_at: when we read it (recording time)

    The pair (as_of, retrieved_at) is the bitemporal handle the Evidence
    Explorer walks. An agent finding without a resolvable Provenance is
    rejected, not surfaced.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_system: str = Field(..., min_length=1)
    source_table: str = Field(..., min_length=1)
    source_pk: str = Field(..., min_length=1)
    field: str = Field(..., min_length=1)
    as_of: datetime
    retrieved_at: datetime

    @field_validator("source_system", "source_table", "source_pk", "field")
    @classmethod
    def _strip_nonempty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("provenance fields cannot be blank")
        return stripped
