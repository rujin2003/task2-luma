"""Cash aggregation and the bank reconciliation artifact.

Two jobs, both controllership-facing.

**Aggregation.** Restricted cash is not spendable and must never be netted into
a liquidity figure — a company with $20M of cash, $6M of it restricted against a
letter of credit, has $14M. Pending items are tracked separately from settled
ones because "available" and "current" are different balances and the difference
is exactly the week-1 forecast error nobody catches.

**Reconciliation.** `WORKFLOW.md` §10 specifies the artifact an accountant
expects: balance per bank, reconciling items, balance per GL, book adjustments,
a difference that is zero, aged exceptions, and a sign-off block. Producing that
correctly is the strongest available signal that the system understands the
domain rather than simulating it — a "discrepancy detected" toast is not a
reconciliation.

The arithmetic follows the ledger, not the presentation. Unrecorded bank fees
reduce the **book** balance, because the bank has already recorded them and we
have not; the same is true of a duplicated journal entry. Outstanding cheques and
deposits in transit adjust the **bank** balance, because we have recorded them
and the bank has not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from backend.contracts.cash import BankRecon, BankReconItem, CashPosition
from backend.contracts.common import MoneyDTO
from backend.finance.errors import PolicyError
from backend.finance.money import Money
from backend.finance.provenance import Provenance

#: Items older than this are exceptions the Controller must look at, not noise.
DEFAULT_AGED_THRESHOLD_DAYS = 30


class ReconItemKind(StrEnum):
    """Which side of the reconciliation an item adjusts."""

    #: Recorded by us, not yet cleared by the bank — reduces the bank balance.
    OUTSTANDING_CHEQUE = "outstanding_cheque"
    #: Received by us, not yet credited by the bank — increases the bank balance.
    DEPOSIT_IN_TRANSIT = "deposit_in_transit"
    #: Charged by the bank, not yet booked by us — reduces the book balance.
    UNRECORDED_BANK_FEE = "unrecorded_bank_fee"
    #: A correction to our own ledger (duplicate JE, misposting) — signed.
    BOOK_ADJUSTMENT = "book_adjustment"


@dataclass(frozen=True, slots=True)
class AccountBalance:
    """One bank account's position at a point in time."""

    account_ref: str
    name: str
    settled: Money
    restricted: bool = False
    pending_in: Money | None = None
    pending_out: Money | None = None

    def _zero(self) -> Money:
        return Money.zero(self.settled.currency)

    @property
    def inflow_pending(self) -> Money:
        return self.pending_in if self.pending_in is not None else self._zero()

    @property
    def outflow_pending(self) -> Money:
        return self.pending_out if self.pending_out is not None else self._zero()


@dataclass(frozen=True, slots=True)
class ReconcilingItem:
    """A single item explaining the gap between bank and book."""

    reference: str
    description: str
    kind: ReconItemKind
    #: Non-negative magnitude for cheques, deposits and fees; signed for
    #: book adjustments, where the direction of a correction is not implied.
    amount: Money
    age_days: int = 0

    def __post_init__(self) -> None:
        if self.age_days < 0:
            raise PolicyError(f"{self.reference}: age_days cannot be negative")
        if self.kind is not ReconItemKind.BOOK_ADJUSTMENT and self.amount.amount < 0:
            raise PolicyError(
                f"{self.reference}: {self.kind} is a magnitude and must be non-negative"
            )


def aggregate(
    balances: tuple[AccountBalance, ...],
    *,
    undrawn_revolver: Money,
    as_of: datetime,
    provenance: tuple[Provenance, ...] = (),
) -> CashPosition:
    """Roll bank accounts into the cash truth the whole product reads from."""
    if not balances:
        raise PolicyError("aggregate requires at least one account balance")
    currencies = {balance.settled.currency.code for balance in balances}
    currencies.add(undrawn_revolver.currency.code)
    if len(currencies) != 1:
        raise PolicyError(f"cash aggregation requires a single currency, got {sorted(currencies)}")

    currency = balances[0].settled.currency
    restricted = Money.sum(
        (balance.settled for balance in balances if balance.restricted), currency
    )
    unrestricted = Money.sum(
        (balance.settled for balance in balances if not balance.restricted), currency
    )
    pending_in = Money.sum((balance.inflow_pending for balance in balances), currency)
    pending_out = Money.sum((balance.outflow_pending for balance in balances), currency)

    return CashPosition(
        current_cash=MoneyDTO.from_money(restricted + unrestricted),
        restricted_cash=MoneyDTO.from_money(restricted),
        unrestricted_cash=MoneyDTO.from_money(unrestricted),
        pending_in=MoneyDTO.from_money(pending_in),
        pending_out=MoneyDTO.from_money(pending_out),
        undrawn_revolver=MoneyDTO.from_money(undrawn_revolver),
        # Available liquidity is unrestricted cash plus committed undrawn
        # capacity. Restricted cash is excluded by definition; pending items are
        # excluded because they have not settled.
        available_liquidity=MoneyDTO.from_money(unrestricted + undrawn_revolver),
        as_of=as_of,
        provenance=provenance,
    )


def _sum_kind(items: tuple[ReconcilingItem, ...], kind: ReconItemKind, currency: Money) -> Money:
    return Money.sum((item.amount for item in items if item.kind is kind), currency.currency)


def reconcile(
    *,
    account_ref: str,
    as_of: date,
    balance_per_bank: Money,
    balance_per_gl: Money,
    items: tuple[ReconcilingItem, ...] = (),
    prepared_by: str = "system",
    reviewed_by: str | None = None,
    approved_by: str | None = None,
    aged_threshold_days: int = DEFAULT_AGED_THRESHOLD_DAYS,
) -> BankRecon:
    """Build the reconciliation artifact.

    A non-zero `difference` is not an error — it is the finding. An unexplained
    gap means a reconciling item is missing, and the artifact must show that
    rather than silently balancing itself, which is why nothing here is plugged.
    """
    currency = balance_per_bank.currency
    if balance_per_gl.currency.code != currency.code:
        raise PolicyError("bank and GL balances must share a currency")
    for item in items:
        if item.amount.currency.code != currency.code:
            raise PolicyError(f"{item.reference}: reconciling item currency differs from account")

    zero = Money.zero(currency)
    cheques = _sum_kind(items, ReconItemKind.OUTSTANDING_CHEQUE, zero)
    deposits = _sum_kind(items, ReconItemKind.DEPOSIT_IN_TRANSIT, zero)
    fees = _sum_kind(items, ReconItemKind.UNRECORDED_BANK_FEE, zero)
    book_adjustments = _sum_kind(items, ReconItemKind.BOOK_ADJUSTMENT, zero)

    adjusted_bank = balance_per_bank - cheques + deposits
    adjusted_book = balance_per_gl + book_adjustments - fees

    aged = tuple(
        BankReconItem(
            reference=item.reference,
            description=item.description,
            amount=MoneyDTO.from_money(item.amount),
            age_days=item.age_days,
            kind=str(item.kind),
        )
        for item in items
        if item.age_days > aged_threshold_days
    )

    return BankRecon(
        account_ref=account_ref,
        as_of=as_of,
        balance_per_bank=MoneyDTO.from_money(balance_per_bank),
        outstanding_cheques=MoneyDTO.from_money(cheques),
        deposits_in_transit=MoneyDTO.from_money(deposits),
        unrecorded_bank_fees=MoneyDTO.from_money(fees),
        adjusted_bank=MoneyDTO.from_money(adjusted_bank),
        balance_per_gl=MoneyDTO.from_money(balance_per_gl),
        book_adjustments=MoneyDTO.from_money(book_adjustments),
        adjusted_book=MoneyDTO.from_money(adjusted_book),
        difference=MoneyDTO.from_money(adjusted_bank - adjusted_book),
        aged_unreconciled=aged,
        prepared_by=prepared_by,
        reviewed_by=reviewed_by,
        approved_by=approved_by,
    )


def render(recon: BankRecon) -> str:
    """The artifact as an accountant reads it (`WORKFLOW.md` §10)."""

    def amount(value: MoneyDTO) -> str:
        return f"{value.to_money().to_major_string():>18}"

    lines = [
        f"Bank reconciliation — {recon.account_ref} — as of {recon.as_of.isoformat()}",
        "",
        f"Balance per bank statement           {amount(recon.balance_per_bank)}",
        f"  Less: outstanding cheques          {amount(recon.outstanding_cheques)}",
        f"  Add:  deposits in transit          {amount(recon.deposits_in_transit)}",
        f"Adjusted bank balance                {amount(recon.adjusted_bank)}",
        "",
        f"Balance per general ledger           {amount(recon.balance_per_gl)}",
        f"  Add:  book adjustments             {amount(recon.book_adjustments)}",
        f"  Less: unrecorded bank fees         {amount(recon.unrecorded_bank_fees)}",
        f"Adjusted book balance                {amount(recon.adjusted_book)}",
        "",
        f"Difference                           {amount(recon.difference)}"
        f"   {'OK' if recon.difference.amount == 0 else 'UNEXPLAINED'}",
    ]
    if recon.aged_unreconciled:
        lines.append("")
        lines.append(
            f"Aged unreconciled items: {len(recon.aged_unreconciled)} — flagged for Controller"
        )
        for item in recon.aged_unreconciled:
            lines.append(
                f"  {item.reference:<16} {item.description:<40} "
                f"{item.amount.to_money().to_major_string():>14}  {item.age_days}d"
            )
    lines.append("")
    lines.append(
        f"Prepared: {recon.prepared_by}  ·  Reviewed: {recon.reviewed_by or '______'}"
        f"  ·  Approved: {recon.approved_by or '______'}"
    )
    return "\n".join(lines)


def is_reconciled(recon: BankRecon) -> bool:
    return recon.difference.amount == 0


__all__ = [
    "DEFAULT_AGED_THRESHOLD_DAYS",
    "AccountBalance",
    "ReconItemKind",
    "ReconcilingItem",
    "aggregate",
    "is_reconciled",
    "reconcile",
    "render",
]
