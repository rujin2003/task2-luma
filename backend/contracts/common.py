"""Shared serializable primitives for contracts and tools."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.finance.currency import get_currency
from backend.finance.money import Money


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MoneyDTO(FrozenModel):
    """Wire form of Money: integer minor units + ISO-4217 code."""

    amount: int
    currency: str = Field(..., min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def _iso(cls, value: str) -> str:
        return get_currency(value).code

    def to_money(self) -> Money:
        return Money.of(self.amount, self.currency)

    @classmethod
    def from_money(cls, money: Money) -> MoneyDTO:
        return cls(amount=money.amount, currency=money.currency.code)
