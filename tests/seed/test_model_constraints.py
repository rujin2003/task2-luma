"""Inconsistent data must be unrepresentable, not merely discouraged.

Each test writes something a careless caller would plausibly write and expects
the database or the posting helper to refuse it. Where a rule cannot be a
row-level CHECK — journal balance, subledger ties — the reconciliation gate is
shown to catch it instead.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from backend.models import (
    Approval,
    Company,
    GLTransaction,
    Invoice,
    Payment,
    PaymentApplication,
    UnbalancedJournal,
    assert_balanced,
    derived_id,
)
from backend.models.invariants import check_invariants
from backend.seed.trivial import (
    AS_OF,
    CURRENCY,
    PARTIAL_PAYMENT_INVOICE,
    PARTIAL_PAYMENT_REF,
    TENANT,
    seed_trivial,
)

COMMON = {"tenant_id": TENANT, "is_synthetic": True}
SRC = {"source_system": "test", "source_table": "t", "source_pk": "pk"}


def _violated(session: Session) -> set[str]:
    return {violation.invariant.name for violation in check_invariants(session)}


def test_unknown_currency_is_rejected(session: Session) -> None:
    session.add(Company(name="Bad Ccy", currency="XYZ", **COMMON))
    with pytest.raises(IntegrityError):
        session.commit()


def test_missing_is_synthetic_fails_loudly(session: Session) -> None:
    """No default: a forgotten flag must not silently mislabel data."""
    session.add(Company(tenant_id=TENANT, name="No Flag", currency=CURRENCY))
    with pytest.raises(IntegrityError):
        session.commit()


def test_gl_line_cannot_be_both_debit_and_credit(session: Session) -> None:
    company_id = seed_trivial(session)
    entry_id = derived_id("journal_entries", company_id, "JE-0001")
    account_id = derived_id("gl_accounts", company_id, "1000")
    session.add(
        GLTransaction(
            entry_id=entry_id,
            account_id=account_id,
            txn_date=date(2026, 8, 1),
            debit_minor=100,
            credit_minor=100,
            currency=CURRENCY,
            effective_at=AS_OF,
            **COMMON,
            **SRC,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_invoice_open_balance_cannot_exceed_its_face_amount(session: Session) -> None:
    company_id = seed_trivial(session)
    customer_id = derived_id("customers", company_id, "Customer A")
    session.add(
        Invoice(
            company_id=company_id,
            customer_id=customer_id,
            invoice_ref="INV-9999",
            issued_date=date(2026, 8, 1),
            due_date=date(2026, 8, 31),
            amount_minor=100_000,
            open_amount_minor=150_000,
            currency=CURRENCY,
            effective_at=AS_OF,
            **COMMON,
            **SRC,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_invoice_due_date_cannot_precede_issue_date(session: Session) -> None:
    company_id = seed_trivial(session)
    session.add(
        Invoice(
            company_id=company_id,
            customer_id=derived_id("customers", company_id, "Customer A"),
            invoice_ref="INV-8888",
            issued_date=date(2026, 8, 31),
            due_date=date(2026, 8, 1),
            amount_minor=1_000,
            open_amount_minor=1_000,
            currency=CURRENCY,
            effective_at=AS_OF,
            **COMMON,
            **SRC,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_a_payment_cannot_be_applied_twice_to_one_invoice(session: Session) -> None:
    company_id = seed_trivial(session)
    session.add(
        PaymentApplication(
            payment_id=derived_id("payments", company_id, PARTIAL_PAYMENT_REF),
            invoice_id=derived_id("invoices", company_id, PARTIAL_PAYMENT_INVOICE),
            amount_minor=1,
            currency=CURRENCY,
            applied_date=date(2026, 8, 20),
            effective_at=AS_OF,
            **COMMON,
            **SRC,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_ingesting_the_same_source_record_twice_is_rejected(session: Session) -> None:
    """Replay safety for Phase 4: one row per origin record, per tenant."""
    company_id = seed_trivial(session)
    duplicate = {
        "source_system": "seed",
        "source_table": "payments",
        "source_pk": PARTIAL_PAYMENT_REF,
    }
    session.add(
        Payment(
            company_id=company_id,
            payment_ref="PMT-DUPLICATE",
            amount_minor=1,
            currency=CURRENCY,
            paid_date=date(2026, 8, 20),
            effective_at=AS_OF,
            **COMMON,
            **duplicate,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_an_approver_cannot_approve_their_own_work(session: Session) -> None:
    """Maker-checker lives in the schema, not only in service code."""
    company_id = seed_trivial(session)
    session.add(
        Approval(
            company_id=company_id,
            action="draw revolver",
            amount_minor=500_000,
            currency=CURRENCY,
            required_role="treasurer",
            prepared_by="alex",
            decided_by="alex",
            decided_at=datetime(2026, 8, 30, tzinfo=UTC),
            decision="approved",
            **COMMON,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_an_approved_decision_needs_a_decider(session: Session) -> None:
    company_id = seed_trivial(session)
    session.add(
        Approval(
            company_id=company_id,
            action="draw revolver",
            amount_minor=500_000,
            currency=CURRENCY,
            required_role="treasurer",
            decision="approved",
            **COMMON,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_unbalanced_journal_is_refused_at_post_time(session: Session) -> None:
    lines = [
        GLTransaction(
            entry_id="e",
            account_id="a",
            txn_date=date(2026, 8, 1),
            debit_minor=100,
            currency=CURRENCY,
            effective_at=AS_OF,
            **COMMON,
            **SRC,
        )
    ]
    with pytest.raises(UnbalancedJournal, match="does not balance"):
        assert_balanced("JE-BAD", lines)


def test_empty_journal_is_refused(session: Session) -> None:
    with pytest.raises(UnbalancedJournal, match="no lines"):
        assert_balanced("JE-EMPTY", [])


def test_gate_catches_a_drifted_invoice_balance(session: Session) -> None:
    company_id = seed_trivial(session)
    session.execute(
        Invoice.__table__.update()
        .where(Invoice.__table__.c.id == derived_id("invoices", company_id, "INV-1002"))
        .values(open_amount_minor=1)
    )
    failures = _violated(session)
    assert "invoice_open_matches_applications" in failures
    assert "ar_subledger_ties_to_gl" in failures


def test_gate_catches_a_posting_dated_after_the_period_cut_off(session: Session) -> None:
    """A closed period may contain postings; none may be dated past its cut-off.

    Closing a period does not make its own entries a violation — that is what a
    closed period *is*. The control is the cut-off: an entry dated after it has
    been posted into the wrong period.
    """
    from backend.models import AccountingPeriod

    company_id = seed_trivial(session)
    period = AccountingPeriod.__table__
    period_id = derived_id("accounting_periods", company_id, "2026-08")

    # Closed with a cut-off after every seeded entry: legitimate, not a finding.
    session.execute(
        period.update()
        .where(period.c.id == period_id)
        .values(status="closed", cutoff=date(2026, 8, 31))
    )
    assert "no_posting_after_a_period_cut_off" not in _violated(session)

    # Pull the cut-off back before the 2026-08-20 receipt: now it is one.
    session.execute(
        period.update().where(period.c.id == period_id).values(cutoff=date(2026, 8, 15))
    )
    assert "no_posting_after_a_period_cut_off" in _violated(session)


def test_gate_catches_a_gap_in_the_event_stream(session: Session) -> None:
    from backend.models import FinancialEvent

    seed_trivial(session)
    session.execute(FinancialEvent.__table__.delete().where(FinancialEvent.__table__.c.seq == 2))
    assert "event_sequence_is_contiguous" in _violated(session)
