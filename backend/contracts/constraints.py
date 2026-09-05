"""Constraints and violations.

Constraints are checked by deterministic code, never by a model. An agent may explain a
violation; it may not decide whether one occurred.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.contracts.money import Money
from backend.contracts.provenance import Evidence


class ConstraintKind(StrEnum):
    MIN_CASH = "min_cash"
    MIN_30D_LIQUIDITY = "min_30d_liquidity"
    MAX_REVOLVER_UTILIZATION = "max_revolver_utilization"
    COVENANT_RATIO = "covenant_ratio"
    PROTECTED_PAYMENT_CLASS = "protected_payment_class"
    MAX_SUPPLIER_DELAY = "max_supplier_delay"
    CLOSED_PERIOD = "closed_period"
    APPROVAL_REQUIRED = "approval_required"


class ConstraintSeverity(StrEnum):
    HARD = "hard"  # a plan violating this is rejected outright
    SOFT = "soft"  # a plan violating this is scored down and flagged


class Constraint(BaseModel):
    """A rule with a threshold, sourced from configuration rather than code."""

    model_config = ConfigDict(frozen=True)

    constraint_id: Annotated[str, Field(min_length=1)]
    kind: ConstraintKind
    severity: ConstraintSeverity
    description: Annotated[str, Field(min_length=1, max_length=280)]
    money_threshold: Money | None = None
    ratio_threshold: Decimal | None = None
    days_threshold: Annotated[int, Field(ge=0)] | None = None
    applies_to: str | None = None
    source_ref: str | None = Field(
        default=None, description="Policy or covenant row this threshold came from."
    )

    @model_validator(mode="after")
    def _has_exactly_one_threshold(self) -> Self:
        if self.kind is ConstraintKind.PROTECTED_PAYMENT_CLASS:
            if self.applies_to is None:
                raise ValueError("a protected payment class must name the class it protects")
            return self
        thresholds = [self.money_threshold, self.ratio_threshold, self.days_threshold]
        provided = [t for t in thresholds if t is not None]
        if len(provided) != 1:
            raise ValueError(f"{self.constraint_id} must carry exactly one threshold")
        return self


class ConstraintViolation(BaseModel):
    """A specific, dated, quantified breach -- never a vibe."""

    model_config = ConfigDict(frozen=True)

    constraint_id: str
    kind: ConstraintKind
    severity: ConstraintSeverity
    description: Annotated[str, Field(min_length=1, max_length=400)]
    observed_money: Money | None = None
    observed_ratio: Decimal | None = None
    observed_days: int | None = None
    threshold_display: str
    week_index: Annotated[int, Field(ge=1, le=13)] | None = None
    evidence: list[Evidence] = Field(default_factory=list)

    def display(self) -> str:
        if self.observed_money is not None:
            observed = str(self.observed_money)
        elif self.observed_ratio is not None:
            observed = str(self.observed_ratio)
        else:
            observed = str(self.observed_days)
        where = f" at W{self.week_index}" if self.week_index else ""
        return f"{self.constraint_id}{where}: {observed} vs {self.threshold_display}"
