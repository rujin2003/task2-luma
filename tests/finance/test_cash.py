"""Cash aggregation and the bank reconciliation artifact (`WORKFLOW.md` §10)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from backend.finance import cash
from backend.finance.cash import AccountBalance, ReconcilingItem, ReconItemKind
from backend.finance.errors import PolicyError
from backend.finance.money import Money

AS_OF = datetime(2026, 8, 30, tzinfo=UTC)
AS_OF_DATE = date(2026, 8, 30)


def usd(minor: int) -> Money:
    return Money.of(minor, "USD")


def test_restricted_cash_is_never_netted_into_liquidity() -> None:
    position = cash.aggregate(
        (
            AccountBalance("op-4471", "Operating", usd(1_241_802_200)),
            AccountBalance("mm-9920", "Money market", usd(400_000_000)),
            AccountBalance("lc-1188", "LC collateral", usd(600_000_000), restricted=True),
        ),
        undrawn_revolver=usd(2_500_000_000),
        as_of=AS_OF,
    )
    assert position.current_cash.amount == 2_241_802_200
    assert position.restricted_cash.amount == 600_000_000
    assert position.unrestricted_cash.amount == 1_641_802_200
    # Available liquidity excludes restricted cash and includes committed capacity.
    assert position.available_liquidity.amount == 1_641_802_200 + 2_500_000_000


def test_pending_items_are_tracked_separately_from_settled() -> None:
    position = cash.aggregate(
        (
            AccountBalance(
                "op-4471",
                "Operating",
                usd(1_000_000),
                pending_in=usd(50_000),
                pending_out=usd(20_000),
            ),
            AccountBalance("op-5512", "Payroll", usd(500_000)),
        ),
        undrawn_revolver=usd(0),
        as_of=AS_OF,
    )
    assert position.pending_in.amount == 50_000
    assert position.pending_out.amount == 20_000
    # Pending cash is not available cash.
    assert position.available_liquidity.amount == 1_500_000


def test_aggregate_guards() -> None:
    with pytest.raises(PolicyError, match="at least one account"):
        cash.aggregate((), undrawn_revolver=usd(0), as_of=AS_OF)
    with pytest.raises(PolicyError, match="single currency"):
        cash.aggregate(
            (
                AccountBalance("a", "A", usd(1)),
                AccountBalance("b", "B", Money.of(1, "EUR")),
            ),
            undrawn_revolver=usd(0),
            as_of=AS_OF,
        )


def test_reconciliation_ties_to_zero() -> None:
    """The `WORKFLOW.md` §10 worked example, with the ledger's own arithmetic.

    Unrecorded bank fees sit on the *book* side: the bank has already charged
    them and we have not booked them. Presenting them under the bank column
    reads more naturally but reconciles to the wrong number.
    """
    items = (
        ReconcilingItem(
            "CHQ-batch", "7 outstanding cheques", ReconItemKind.OUTSTANDING_CHEQUE, usd(18_430_000)
        ),
        ReconcilingItem(
            "DEP-batch", "3 deposits in transit", ReconItemKind.DEPOSIT_IN_TRANSIT, usd(27_190_000)
        ),
        ReconcilingItem(
            "FEE-0826", "unrecorded bank fees", ReconItemKind.UNRECORDED_BANK_FEE, usd(214_000)
        ),
        ReconcilingItem(
            "JE-88214",
            "duplicate journal entry reversed",
            ReconItemKind.BOOK_ADJUSTMENT,
            usd(-344_000),
        ),
    )
    recon = cash.reconcile(
        account_ref="op-4471",
        as_of=AS_OF_DATE,
        balance_per_bank=usd(1_241_802_200),
        balance_per_gl=usd(1_251_120_200),
        items=items,
    )
    assert recon.adjusted_bank.amount == 1_241_802_200 - 18_430_000 + 27_190_000
    assert recon.adjusted_book.amount == 1_251_120_200 - 344_000 - 214_000
    assert recon.difference.amount == 0
    assert cash.is_reconciled(recon)
    assert recon.prepared_by == "system"
    assert recon.reviewed_by is None


def test_an_unexplained_gap_is_reported_not_plugged() -> None:
    recon = cash.reconcile(
        account_ref="op-4471",
        as_of=AS_OF_DATE,
        balance_per_bank=usd(1_000_000),
        balance_per_gl=usd(1_003_440),
    )
    assert recon.difference.amount == -3_440
    assert not cash.is_reconciled(recon)
    assert "UNEXPLAINED" in cash.render(recon)


def test_aged_items_are_flagged_for_the_controller() -> None:
    items = (
        ReconcilingItem(
            "CHQ-1001", "stale cheque", ReconItemKind.OUTSTANDING_CHEQUE, usd(4_500), age_days=94
        ),
        ReconcilingItem(
            "CHQ-1002", "recent cheque", ReconItemKind.OUTSTANDING_CHEQUE, usd(9_000), age_days=6
        ),
        ReconcilingItem(
            "DEP-2001", "aged deposit", ReconItemKind.DEPOSIT_IN_TRANSIT, usd(1_200), age_days=41
        ),
    )
    recon = cash.reconcile(
        account_ref="op-4471",
        as_of=AS_OF_DATE,
        balance_per_bank=usd(1_000_000),
        balance_per_gl=usd(1_012_300),
        items=items,
    )
    assert [item.reference for item in recon.aged_unreconciled] == ["CHQ-1001", "DEP-2001"]
    rendered = cash.render(recon)
    assert "flagged for Controller" in rendered
    assert "CHQ-1002" not in rendered


def test_reconciliation_guards() -> None:
    with pytest.raises(PolicyError, match="share a currency"):
        cash.reconcile(
            account_ref="op-4471",
            as_of=AS_OF_DATE,
            balance_per_bank=usd(1),
            balance_per_gl=Money.of(1, "EUR"),
        )
    with pytest.raises(PolicyError, match="currency differs from account"):
        cash.reconcile(
            account_ref="op-4471",
            as_of=AS_OF_DATE,
            balance_per_bank=usd(1),
            balance_per_gl=usd(1),
            items=(
                ReconcilingItem("x", "x", ReconItemKind.OUTSTANDING_CHEQUE, Money.of(1, "EUR")),
            ),
        )


def test_reconciling_item_guards() -> None:
    with pytest.raises(PolicyError, match="age_days cannot be negative"):
        ReconcilingItem("x", "x", ReconItemKind.OUTSTANDING_CHEQUE, usd(1), age_days=-1)
    with pytest.raises(PolicyError, match="must be non-negative"):
        ReconcilingItem("x", "x", ReconItemKind.DEPOSIT_IN_TRANSIT, usd(-1))
    # A book adjustment is signed: a duplicate entry reduces the book balance.
    assert ReconcilingItem("x", "x", ReconItemKind.BOOK_ADJUSTMENT, usd(-1)).amount.amount == -1


def test_render_contains_the_sign_off_block() -> None:
    recon = cash.reconcile(
        account_ref="op-4471",
        as_of=AS_OF_DATE,
        balance_per_bank=usd(1_000_000),
        balance_per_gl=usd(1_000_000),
        prepared_by="system",
        reviewed_by="a.rivera",
    )
    rendered = cash.render(recon)
    assert "Prepared: system" in rendered
    assert "Reviewed: a.rivera" in rendered
    assert "Approved: ______" in rendered
    assert "Bank reconciliation — op-4471 — as of 2026-08-30" in rendered
