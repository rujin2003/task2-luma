from __future__ import annotations

from datetime import date, datetime

from backend.contracts.common import FrozenModel, MoneyDTO
from backend.finance.provenance import Provenance


class CashPosition(FrozenModel):
    current_cash: MoneyDTO
    restricted_cash: MoneyDTO
    unrestricted_cash: MoneyDTO
    pending_in: MoneyDTO
    pending_out: MoneyDTO
    undrawn_revolver: MoneyDTO
    available_liquidity: MoneyDTO
    as_of: datetime
    provenance: tuple[Provenance, ...] = ()


class BankReconItem(FrozenModel):
    reference: str
    description: str
    amount: MoneyDTO
    age_days: int
    kind: str


class BankRecon(FrozenModel):
    """Controllership artifact — book, bank, reconciling items, sign-off."""

    account_ref: str
    as_of: date
    balance_per_bank: MoneyDTO
    outstanding_cheques: MoneyDTO
    deposits_in_transit: MoneyDTO
    unrecorded_bank_fees: MoneyDTO
    adjusted_bank: MoneyDTO
    balance_per_gl: MoneyDTO
    book_adjustments: MoneyDTO
    adjusted_book: MoneyDTO
    difference: MoneyDTO
    aged_unreconciled: tuple[BankReconItem, ...] = ()
    prepared_by: str
    reviewed_by: str | None = None
    approved_by: str | None = None
