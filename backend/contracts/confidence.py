"""Confidence.

A model asserting `"confidence": 0.87` is unfalsifiable, and a treasurer will distrust
it on sight. Where we have measured error we report measured error; where we only have
a judgement we say so and label it. The two are different types of claim and the UI
renders them differently.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConfidenceBasis(StrEnum):
    EMPIRICAL = "empirical"
    QUALITATIVE = "qualitative"


class ConfidenceBand(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


Percent = Annotated[Decimal, Field(ge=0, le=100)]


class Confidence(BaseModel):
    """Either measured forecast error, or a labelled judgement -- never a bare float."""

    model_config = ConfigDict(frozen=True)

    basis: ConfidenceBasis
    rationale: Annotated[str, Field(min_length=1, max_length=280)]

    # empirical
    mape_pct: Percent | None = None
    sample_size: Annotated[int, Field(ge=1)] | None = None
    horizon_weeks: Annotated[int, Field(ge=1, le=13)] | None = None

    # qualitative
    band: ConfidenceBand | None = None

    @model_validator(mode="after")
    def _basis_matches_fields(self) -> Self:
        if self.basis is ConfidenceBasis.EMPIRICAL:
            if self.mape_pct is None or self.sample_size is None:
                raise ValueError("empirical confidence requires mape_pct and sample_size")
            if self.band is not None:
                raise ValueError("empirical confidence must not also carry a qualitative band")
        else:
            if self.band is None:
                raise ValueError("qualitative confidence requires a band")
            if self.mape_pct is not None or self.sample_size is not None:
                raise ValueError("qualitative confidence must not claim measured error")
        return self

    @classmethod
    def empirical(
        cls, *, mape_pct: Decimal, sample_size: int, horizon_weeks: int, rationale: str
    ) -> Confidence:
        return cls(
            basis=ConfidenceBasis.EMPIRICAL,
            mape_pct=mape_pct,
            sample_size=sample_size,
            horizon_weeks=horizon_weeks,
            rationale=rationale,
        )

    @classmethod
    def qualitative(cls, *, band: ConfidenceBand, rationale: str) -> Confidence:
        return cls(basis=ConfidenceBasis.QUALITATIVE, band=band, rationale=rationale)

    def display(self) -> str:
        if self.basis is ConfidenceBasis.EMPIRICAL:
            return f"MAPE {self.mape_pct}% at W{self.horizon_weeks} over {self.sample_size} weeks"
        assert self.band is not None
        return f"{self.band.value} (judgement)"
