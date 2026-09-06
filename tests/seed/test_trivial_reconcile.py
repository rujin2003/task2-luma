"""Phase 1 exit criteria: bank, AR and GL reconcile via SQL alone.

The exit criteria is expressed twice on purpose. The named balance assertions
document the hand-worked numbers a reviewer can check by eye; the invariant gate
proves the same thing structurally, over every row, and keeps proving it as the
dataset grows in Phase 2.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlmodel import Session

from backend.models.invariants import INVARIANTS, assert_consistent, check_invariants
from backend.seed.trivial import (
    CLOSING_AR_MINOR,
    CLOSING_CASH_MINOR,
    INVOICES,
    PARTIAL_PAYMENT_MINOR,
    TENANT,
    seed_trivial,
)


def _scalar(session: Session, sql: str) -> int:
    value = session.execute(text(sql), {"tenant": TENANT}).one()[0]
    return 0 if value is None else int(value)


def test_ar_subledger_ties_to_the_control_account(session: Session) -> None:
    seed_trivial(session)

    open_ar = _scalar(
        session,
        """
        SELECT SUM(open_amount_minor) FROM invoices
        WHERE tenant_id = :tenant AND status IN ('open', 'part_paid')
        """,
    )
    gl_ar = _scalar(
        session,
        """
        SELECT SUM(t.debit_minor) - SUM(t.credit_minor)
        FROM gl_transactions t JOIN gl_accounts a ON a.id = t.account_id
        WHERE a.role = 'ar_trade' AND a.tenant_id = :tenant
        """,
    )
    assert open_ar == gl_ar == CLOSING_AR_MINOR


def test_bank_ties_to_gl_cash_after_cash_moves(session: Session) -> None:
    seed_trivial(session)

    settled = _scalar(
        session,
        """
        SELECT SUM(t.amount_minor) FROM bank_transactions t
        WHERE t.tenant_id = :tenant AND t.pending = FALSE
        """,
    )
    stored = _scalar(
        session,
        """
        SELECT current_balance_minor FROM bank_accounts
        WHERE tenant_id = :tenant AND account_ref = 'op-4471'
        """,
    )
    gl_cash = _scalar(
        session,
        """
        SELECT SUM(t.debit_minor) - SUM(t.credit_minor)
        FROM gl_transactions t JOIN gl_accounts a ON a.id = t.account_id
        WHERE a.role = 'cash_operating' AND a.tenant_id = :tenant
        """,
    )
    assert settled == stored == gl_cash == CLOSING_CASH_MINOR


def test_every_journal_entry_balances(session: Session) -> None:
    seed_trivial(session)

    unbalanced = _scalar(
        session,
        """
        SELECT COUNT(*) FROM (
            SELECT j.id FROM journal_entries j
            JOIN gl_transactions t ON t.entry_id = j.id
            WHERE j.tenant_id = :tenant
            GROUP BY j.id
            HAVING SUM(t.debit_minor) <> SUM(t.credit_minor)
        ) AS bad
        """,
    )
    assert unbalanced == 0


def test_partial_payment_is_represented_not_hidden(session: Session) -> None:
    """The seed must exercise applied cash — three untouched invoices prove nothing."""
    seed_trivial(session)

    applied = _scalar(
        session,
        "SELECT SUM(amount_minor) FROM payment_applications WHERE tenant_id = :tenant",
    )
    part_paid = _scalar(
        session,
        "SELECT COUNT(*) FROM invoices WHERE tenant_id = :tenant AND status = 'part_paid'",
    )
    issued = _scalar(session, "SELECT COUNT(*) FROM invoices WHERE tenant_id = :tenant")

    assert applied == PARTIAL_PAYMENT_MINOR
    assert part_paid == 1
    assert issued == len(INVOICES)


def test_reconciliation_gate_is_clean(session: Session) -> None:
    seed_trivial(session)
    assert check_invariants(session) == []
    assert_consistent(session)


def test_gate_runs_on_an_empty_database(session: Session) -> None:
    """No rows is a consistent state, not an error — Phase 2A relies on this."""
    assert check_invariants(session) == []


def test_invariant_names_are_unique() -> None:
    names = [invariant.name for invariant in INVARIANTS]
    assert len(names) == len(set(names))
