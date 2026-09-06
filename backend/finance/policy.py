"""Versioned TreasuryPolicy. Thresholds live here, never in engine code."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.finance.currency import get_currency
from backend.finance.errors import PolicyError
from backend.finance.money import Money

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "treasury_policy.yaml"

PROTECTED_CLASSES = frozenset({"payroll", "tax", "statutory"})


class MoneyLiteral(BaseModel):
    """Serializable money pair used in config and API contracts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    amount: int
    currency: str

    @field_validator("currency")
    @classmethod
    def _known_currency(cls, value: str) -> str:
        return get_currency(value).code

    def to_money(self) -> Money:
        return Money.of(self.amount, self.currency)


class MaterialityThreshold(BaseModel):
    """Resolved as the lesser of a fixed floor and a % of monthly opex."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    absolute: MoneyLiteral
    pct_of_monthly_opex_bps: int = Field(..., ge=0, le=100_00)

    def resolve(self, monthly_opex: Money) -> Money:
        if monthly_opex.currency.code != self.absolute.currency:
            raise PolicyError(
                f"opex currency {monthly_opex.currency.code} != "
                f"materiality currency {self.absolute.currency}"
            )
        percent = monthly_opex.amount * self.pct_of_monthly_opex_bps // 10_000
        floor = self.absolute.to_money()
        chosen = percent if percent < floor.amount else floor.amount
        return Money.of(chosen, floor.currency)


class DoABand(BaseModel):
    """One row of the delegation-of-authority matrix."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: str
    max_amount: MoneyLiteral | None = None
    approver: str
    blocked: bool = False


class TreasuryPolicy(BaseModel):
    """Configurable treasury constraints. Versioned; never hard-coded in engines."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(..., ge=1)
    effective_at: datetime
    currency: str
    min_unrestricted_cash: MoneyLiteral
    min_30d_liquidity: MoneyLiteral
    max_revolver_utilization_bps: int = Field(..., ge=0, le=10_000)
    protected_payment_classes: tuple[str, ...]
    materiality: MaterialityThreshold
    doa_bands: tuple[DoABand, ...] = ()

    @field_validator("currency")
    @classmethod
    def _known_currency(cls, value: str) -> str:
        return get_currency(value).code

    @field_validator("protected_payment_classes")
    @classmethod
    def _known_classes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().lower() for item in value)
        unknown = set(normalized) - PROTECTED_CLASSES
        if unknown:
            raise PolicyError(f"unknown protected payment classes: {sorted(unknown)}")
        if "payroll" not in normalized or "tax" not in normalized:
            raise PolicyError("policy must protect payroll and tax")
        return normalized

    @model_validator(mode="after")
    def _single_currency(self) -> TreasuryPolicy:
        expected = self.currency
        for label, money in (
            ("min_unrestricted_cash", self.min_unrestricted_cash),
            ("min_30d_liquidity", self.min_30d_liquidity),
            ("materiality.absolute", self.materiality.absolute),
        ):
            if money.currency != expected:
                raise PolicyError(f"{label} currency {money.currency} != policy {expected}")
        return self

    def is_protected(self, payment_class: str) -> bool:
        return payment_class.strip().lower() in self.protected_payment_classes

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> TreasuryPolicy:
        try:
            return cls.model_validate(data)
        except Exception as exc:
            if isinstance(exc, PolicyError):
                raise
            raise PolicyError(str(exc)) from exc

    @classmethod
    def load(cls, path: Path | None = None) -> TreasuryPolicy:
        policy_path = path or DEFAULT_POLICY_PATH
        try:
            raw = policy_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PolicyError(f"cannot read TreasuryPolicy at {policy_path}") from exc
        loaded = yaml.safe_load(raw)
        if not isinstance(loaded, dict):
            raise PolicyError("TreasuryPolicy YAML must be a mapping")
        return cls.from_mapping(loaded)
