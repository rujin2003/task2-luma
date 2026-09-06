"""The reconciliation gate: consistency invariants as committed SQL.

Every invariant is a query that returns the *violating* rows, so "the data is
consistent" means "every query returns nothing". That phrasing matters: a check
written as an equality assertion in a test tells you only that one hand-picked
number matched, while a query that hunts for violations scales to the full
seeded dataset and to a customer's real schema.

These are reused by Phase 2 seeding (enforced at generation, re-checked in
tests) and by Phase 2A's schema-adaptation reconciliation gate, which is why
they live in the model layer rather than in a test file.

All SQL is plain ANSI so it runs identically on SQLite and PostgreSQL.

Two of these are stated more carefully than the obvious form, because the
obvious form is wrong against real data:

* **Cut-off, not "no postings in a closed period."** Closed periods are full of
  postings; that is what closing one means. The control an accountant actually
  wants is that nothing is *dated after the cut-off* of the period it sits in.
* **Bank ties to GL, unless it is reconciled.** A bank balance that differs from
  GL cash is normal — outstanding cheques, deposits in transit, fees the bank has
  booked and we have not. What must hold is that the difference is *explained*:
  an account with a reconciliation must reconcile to zero against the ledger, and
  only an unreconciled account is required to agree outright. Demanding equality
  everywhere would force the seed to omit the reconciliation exercise entirely,
  which is the one controllership artifact `WORKFLOW.md` §10 asks for.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True, slots=True)
class Invariant:
    name: str
    description: str
    sql: str


@dataclass(frozen=True, slots=True)
class Violation:
    invariant: Invariant
    count: int
    sample: tuple[tuple[object, ...], ...]

    def __str__(self) -> str:
        return (
            f"{self.invariant.name}: {self.count} violating row(s) — {self.invariant.description}"
        )


INVARIANTS: tuple[Invariant, ...] = (
    Invariant(
        "invoice_open_matches_applications",
        "invoice open balance equals face amount less applied cash",
        """
        SELECT i.id, i.invoice_ref, i.open_amount_minor, i.amount_minor
        FROM invoices i
        WHERE i.open_amount_minor <> i.amount_minor - COALESCE((
            SELECT SUM(pa.amount_minor) FROM payment_applications pa
            WHERE pa.invoice_id = i.id
        ), 0)
        """,
    ),
    Invariant(
        "invoice_status_matches_open_amount",
        "invoice status agrees with its open balance",
        """
        SELECT i.id, i.invoice_ref, i.status, i.open_amount_minor
        FROM invoices i
        WHERE (i.status = 'paid' AND i.open_amount_minor <> 0)
           OR (i.status = 'open' AND i.open_amount_minor <> i.amount_minor)
           OR (i.status = 'part_paid'
               AND (i.open_amount_minor = 0 OR i.open_amount_minor = i.amount_minor))
        """,
    ),
    Invariant(
        "payments_not_over_applied",
        "applied cash never exceeds the payment received",
        """
        SELECT p.id, p.payment_ref, p.amount_minor
        FROM payments p
        WHERE COALESCE((
            SELECT SUM(pa.amount_minor) FROM payment_applications pa WHERE pa.payment_id = p.id
        ), 0) > p.amount_minor
        """,
    ),
    Invariant(
        "payment_application_currency_agrees",
        "an application matches the currency of both its payment and its invoice",
        """
        SELECT pa.id
        FROM payment_applications pa
        JOIN payments p ON p.id = pa.payment_id
        JOIN invoices i ON i.id = pa.invoice_id
        WHERE pa.currency <> p.currency OR pa.currency <> i.currency
        """,
    ),
    Invariant(
        "journal_entries_balance",
        "every journal entry has debits equal to credits",
        """
        SELECT j.id, j.entry_ref,
               SUM(t.debit_minor) AS debits, SUM(t.credit_minor) AS credits
        FROM journal_entries j
        JOIN gl_transactions t ON t.entry_id = j.id
        GROUP BY j.id, j.entry_ref
        HAVING SUM(t.debit_minor) <> SUM(t.credit_minor)
        """,
    ),
    Invariant(
        "journal_entries_have_lines",
        "no journal entry is empty",
        """
        SELECT j.id, j.entry_ref
        FROM journal_entries j
        WHERE NOT EXISTS (SELECT 1 FROM gl_transactions t WHERE t.entry_id = j.id)
        """,
    ),
    Invariant(
        "gl_line_agrees_with_entry",
        "GL lines share their entry's date and currency",
        """
        SELECT t.id
        FROM gl_transactions t
        JOIN journal_entries j ON j.id = t.entry_id
        WHERE t.txn_date <> j.txn_date OR t.currency <> j.currency OR t.tenant_id <> j.tenant_id
        """,
    ),
    Invariant(
        "no_posting_after_a_period_cut_off",
        "no journal entry is dated after the cut-off of the closed period it sits in",
        """
        SELECT j.id, j.entry_ref, p.name, j.txn_date, p.cutoff
        FROM journal_entries j
        JOIN accounting_periods p ON p.id = j.period_id
        WHERE p.status <> 'open'
          AND p.cutoff IS NOT NULL
          AND j.txn_date > p.cutoff
        """,
    ),
    Invariant(
        "bank_balance_matches_transactions",
        "a bank account's stored balance equals its settled transactions",
        """
        SELECT b.id, b.account_ref, b.current_balance_minor
        FROM bank_accounts b
        WHERE b.current_balance_minor <> COALESCE((
            SELECT SUM(t.amount_minor) FROM bank_transactions t
            WHERE t.account_id = b.id AND t.pending = FALSE
        ), 0)
        """,
    ),
    Invariant(
        "bank_ties_to_gl_cash",
        "an unreconciled bank account's balance equals its mapped GL cash account",
        """
        SELECT b.id, b.account_ref, b.current_balance_minor
        FROM bank_accounts b
        JOIN gl_accounts a ON a.id = b.gl_account_id
        WHERE NOT EXISTS (
            SELECT 1 FROM bank_reconciliations r WHERE r.account_id = b.id
        )
        AND b.current_balance_minor <> COALESCE((
            SELECT SUM(t.debit_minor) - SUM(t.credit_minor) FROM gl_transactions t
            WHERE t.account_id = a.id
        ), 0)
        """,
    ),
    Invariant(
        "bank_reconciliation_ties",
        "a reconciled account's stated balances match the ledger and the difference is zero",
        """
        SELECT r.id, r.account_id, r.difference_minor
        FROM bank_reconciliations r
        JOIN bank_accounts b ON b.id = r.account_id
        JOIN gl_accounts a ON a.id = b.gl_account_id
        WHERE r.difference_minor <> 0
           OR r.balance_per_bank_minor <> b.current_balance_minor
           OR r.balance_per_gl_minor <> COALESCE((
                SELECT SUM(t.debit_minor) - SUM(t.credit_minor) FROM gl_transactions t
                WHERE t.account_id = a.id
              ), 0)
        """,
    ),
    Invariant(
        "ar_subledger_ties_to_gl",
        "open AR equals the trade receivables control account, per company",
        """
        SELECT c.id, c.name
        FROM companies c
        WHERE COALESCE((
                SELECT SUM(i.open_amount_minor) FROM invoices i
                WHERE i.company_id = c.id AND i.status IN ('open', 'part_paid')
              ), 0)
            <> COALESCE((
                SELECT SUM(t.debit_minor) - SUM(t.credit_minor)
                FROM gl_transactions t
                JOIN gl_accounts a ON a.id = t.account_id
                WHERE a.company_id = c.id AND a.role = 'ar_trade'
              ), 0)
        """,
    ),
    Invariant(
        "ap_subledger_ties_to_gl",
        "open AP equals the trade payables control account, per company",
        """
        SELECT c.id, c.name
        FROM companies c
        WHERE COALESCE((
                SELECT SUM(v.open_amount_minor) FROM vendor_invoices v
                WHERE v.company_id = c.id AND v.status IN ('open', 'part_paid')
              ), 0)
            <> COALESCE((
                SELECT SUM(t.credit_minor) - SUM(t.debit_minor)
                FROM gl_transactions t
                JOIN gl_accounts a ON a.id = t.account_id
                WHERE a.company_id = c.id AND a.role = 'ap_trade'
              ), 0)
        """,
    ),
    Invariant(
        "event_sequence_is_contiguous",
        "the append-only event stream has no gaps, so a replay is total",
        """
        SELECT e.company_id, MAX(e.seq) AS max_seq, COUNT(*) AS n
        FROM financial_events e
        GROUP BY e.company_id
        HAVING MAX(e.seq) <> COUNT(*)
        """,
    ),
    Invariant(
        "every_fact_has_an_event",
        "invoices are represented in the append-only ledger they project from",
        """
        SELECT i.id, i.invoice_ref
        FROM invoices i
        WHERE NOT EXISTS (
            SELECT 1 FROM financial_events e
            WHERE e.entity_table = 'invoices' AND e.entity_pk = i.invoice_ref
              AND e.tenant_id = i.tenant_id
        )
        """,
    ),
    Invariant(
        "forecast_watermark_not_in_the_future",
        "a forecast never claims to know data recorded after it was built",
        """
        SELECT v.id, v.version_label
        FROM forecast_versions v
        WHERE v.data_recorded_through > v.as_of
        """,
    ),
    Invariant(
        "forecast_lines_within_horizon",
        "every forecast line sits inside its version's declared horizon",
        """
        SELECT l.id, l.week_index
        FROM forecast_lines l
        JOIN forecast_versions v ON v.id = l.version_id
        WHERE l.week_index > v.horizon_weeks OR l.currency <> v.currency
        """,
    ),
    Invariant(
        "no_cross_tenant_references",
        "a child row never points at a parent owned by another tenant",
        """
        SELECT 'invoices' AS tbl, i.id FROM invoices i
          JOIN customers c ON c.id = i.customer_id WHERE c.tenant_id <> i.tenant_id
        UNION ALL
        SELECT 'payment_applications', pa.id FROM payment_applications pa
          JOIN invoices i ON i.id = pa.invoice_id WHERE i.tenant_id <> pa.tenant_id
        UNION ALL
        SELECT 'gl_transactions', t.id FROM gl_transactions t
          JOIN gl_accounts a ON a.id = t.account_id WHERE a.tenant_id <> t.tenant_id
        UNION ALL
        SELECT 'bank_transactions', bt.id FROM bank_transactions bt
          JOIN bank_accounts b ON b.id = bt.account_id WHERE b.tenant_id <> bt.tenant_id
        UNION ALL
        SELECT 'forecast_lines', l.id FROM forecast_lines l
          JOIN forecast_versions v ON v.id = l.version_id WHERE v.tenant_id <> l.tenant_id
        """,
    ),
    Invariant(
        "synthetic_data_does_not_mix",
        "demo and production rows never share a company",
        """
        SELECT 'invoices' AS tbl, i.id FROM invoices i
          JOIN companies c ON c.id = i.company_id WHERE c.is_synthetic <> i.is_synthetic
        UNION ALL
        SELECT 'bank_accounts', b.id FROM bank_accounts b
          JOIN companies c ON c.id = b.company_id WHERE c.is_synthetic <> b.is_synthetic
        UNION ALL
        SELECT 'journal_entries', j.id FROM journal_entries j
          JOIN companies c ON c.id = j.company_id WHERE c.is_synthetic <> j.is_synthetic
        """,
    ),
    Invariant(
        "worklist_maker_is_not_checker",
        "the preparer of a worklist item is never its reviewer",
        """
        SELECT w.id FROM worklist_items w
        WHERE w.prepared_by IS NOT NULL AND w.reviewed_by IS NOT NULL
          AND w.prepared_by = w.reviewed_by
        """,
    ),
)


def check_invariants(
    session: Session, *, sample_rows: int = 3, only: tuple[str, ...] | None = None
) -> list[Violation]:
    """Run the gate. An empty list means the dataset is internally consistent."""
    selected = INVARIANTS if only is None else tuple(i for i in INVARIANTS if i.name in only)
    if only is not None and len(selected) != len(only):
        missing = set(only) - {i.name for i in selected}
        raise KeyError(f"unknown invariant(s): {sorted(missing)}")
    violations: list[Violation] = []
    for invariant in selected:
        rows = session.execute(text(invariant.sql)).fetchall()
        if rows:
            violations.append(
                Violation(
                    invariant=invariant,
                    count=len(rows),
                    sample=tuple(tuple(row) for row in rows[:sample_rows]),
                )
            )
    return violations


def assert_consistent(session: Session) -> None:
    """Raise with every failing invariant named, not just the first."""
    violations = check_invariants(session)
    if violations:
        detail = "\n".join(f"  - {violation}" for violation in violations)
        raise AssertionError(f"{len(violations)} invariant(s) violated:\n{detail}")
