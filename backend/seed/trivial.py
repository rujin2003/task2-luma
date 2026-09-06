"""Phase 1 exit seed: a small company whose bank, AR and GL tie in SQL alone.

Deliberately not the *simplest* dataset that reconciles. Three untouched
invoices reconcile trivially — every balance is its own opening balance and
nothing is exercised. This one includes a partial payment, so the AR subledger,
the control account and the bank all have to agree after cash has moved, which
is the first place a naive model breaks.

Everything is keyed by `derived_id`, so re-running produces byte-identical rows.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlmodel import Session

from backend.models import (
    AccountingPeriod,
    BankAccount,
    BankTransaction,
    Company,
    Customer,
    EventLog,
    GLAccount,
    GLTransaction,
    Invoice,
    JournalEntry,
    Payment,
    PaymentApplication,
    assert_balanced,
    derived_id,
)

TENANT = "novatech-trivial"
AS_OF = datetime(2026, 8, 30, tzinfo=UTC)
BOOK_DATE = date(2026, 8, 1)
PAY_DATE = date(2026, 8, 20)
CURRENCY = "USD"
IS_SYNTHETIC = True

# $10,000.00 opening cash; $5,000.00 of AR issued; $1,200.00 collected in part.
OPENING_CASH_MINOR = 1_000_000
INVOICES: tuple[tuple[str, str, int], ...] = (
    ("INV-1001", "Customer A", 200_000),
    ("INV-1002", "Customer B", 150_000),
    ("INV-1003", "Customer C", 150_000),
)
PARTIAL_PAYMENT_REF = "PMT-2001"
PARTIAL_PAYMENT_INVOICE = "INV-1001"
PARTIAL_PAYMENT_MINOR = 120_000

AR_ISSUED_MINOR = sum(amount for _, _, amount in INVOICES)
CLOSING_CASH_MINOR = OPENING_CASH_MINOR + PARTIAL_PAYMENT_MINOR
CLOSING_AR_MINOR = AR_ISSUED_MINOR - PARTIAL_PAYMENT_MINOR

_ACCOUNTS: tuple[tuple[str, str, str, bool], ...] = (
    ("1000", "Cash - Operating", "cash_operating", True),
    ("1200", "AR - Trade", "ar_trade", True),
    ("3000", "Opening Equity", "opening_equity", False),
    ("4000", "Revenue", "revenue", False),
)


def _src(table: str, pk: str) -> dict[str, str]:
    return {"source_system": "seed", "source_table": table, "source_pk": pk}


def seed_trivial(session: Session) -> str:
    """Insert the seed company and return its id. Idempotent for a given tenant."""
    company_id = derived_id("companies", TENANT, "NovaTech Trivial")
    log = EventLog(TENANT, company_id, is_synthetic=IS_SYNTHETIC)
    common = {"tenant_id": TENANT, "is_synthetic": IS_SYNTHETIC}

    session.add(
        Company(
            id=company_id,
            name="NovaTech Trivial",
            currency=CURRENCY,
            country="US",
            business_timezone="America/New_York",
            **common,
        )
    )
    session.add(
        log.emit(
            "company_registered", "companies", "NovaTech Trivial", {"currency": CURRENCY}, AS_OF
        )
    )
    # Flush between layers: foreign keys are enforced, so parents must exist first.
    session.flush()

    period_id = derived_id("accounting_periods", company_id, "2026-08")
    session.add(
        AccountingPeriod(
            id=period_id,
            company_id=company_id,
            name="2026-08",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
            status="open",
            **common,
        )
    )
    session.flush()

    accounts: dict[str, str] = {}
    for number, name, role, normal_debit in _ACCOUNTS:
        account_id = derived_id("gl_accounts", company_id, number)
        accounts[role] = account_id
        session.add(
            GLAccount(
                id=account_id,
                company_id=company_id,
                account_number=number,
                name=name,
                role=role,
                currency=CURRENCY,
                normal_debit=normal_debit,
                **common,
                **_src("gl_accounts", number),
            )
        )
        session.add(log.emit("gl_account_opened", "gl_accounts", number, {"role": role}, AS_OF))
    session.flush()

    bank_id = derived_id("bank_accounts", company_id, "op-4471")
    session.add(
        BankAccount(
            id=bank_id,
            company_id=company_id,
            gl_account_id=accounts["cash_operating"],
            name="Operating",
            account_ref="op-4471",
            currency=CURRENCY,
            current_balance_minor=CLOSING_CASH_MINOR,
            effective_at=AS_OF,
            recorded_at=AS_OF,
            **common,
            **_src("bank_accounts", "op-4471"),
        )
    )
    session.add(
        log.emit("bank_account_opened", "bank_accounts", "op-4471", {"currency": CURRENCY}, AS_OF)
    )
    session.flush()

    def post_journal(
        entry_ref: str,
        txn_date: date,
        memo: str,
        legs: tuple[tuple[str, int, int], ...],
    ) -> None:
        """Post a balanced entry. Legs are (role, debit_minor, credit_minor)."""
        entry_id = derived_id("journal_entries", company_id, entry_ref)
        entry = JournalEntry(
            id=entry_id,
            company_id=company_id,
            period_id=period_id,
            entry_ref=entry_ref,
            txn_date=txn_date,
            currency=CURRENCY,
            memo=memo,
            effective_at=AS_OF,
            recorded_at=AS_OF,
            **common,
            **_src("journal_entries", entry_ref),
        )
        lines = [
            GLTransaction(
                id=derived_id("gl_transactions", entry_id, str(index)),
                entry_id=entry_id,
                account_id=accounts[role],
                txn_date=txn_date,
                debit_minor=debit,
                credit_minor=credit,
                currency=CURRENCY,
                memo=memo,
                line_no=index,
                effective_at=AS_OF,
                recorded_at=AS_OF,
                **common,
                **_src("gl_transactions", f"{entry_ref}-{index}"),
            )
            for index, (role, debit, credit) in enumerate(legs, start=1)
        ]
        assert_balanced(entry_ref, lines)
        session.add(entry)
        session.flush()
        for line in lines:
            session.add(line)
        session.add(
            log.emit(
                "journal_posted",
                "journal_entries",
                entry_ref,
                {"memo": memo, "legs": [[role, dr, cr] for role, dr, cr in legs]},
                AS_OF,
            )
        )

    post_journal(
        "JE-0001",
        BOOK_DATE,
        "opening cash",
        (("cash_operating", OPENING_CASH_MINOR, 0), ("opening_equity", 0, OPENING_CASH_MINOR)),
    )
    post_journal(
        "JE-0002",
        BOOK_DATE,
        "invoices issued",
        (("ar_trade", AR_ISSUED_MINOR, 0), ("revenue", 0, AR_ISSUED_MINOR)),
    )

    for ref, customer_name, amount in INVOICES:
        customer_id = derived_id("customers", company_id, customer_name)
        session.add(
            Customer(
                id=customer_id,
                company_id=company_id,
                name=customer_name,
                **common,
                **_src("customers", customer_name),
            )
        )
        session.add(log.emit("customer_registered", "customers", customer_name, {}, AS_OF))
        session.flush()

        paid = PARTIAL_PAYMENT_MINOR if ref == PARTIAL_PAYMENT_INVOICE else 0
        session.add(
            Invoice(
                id=derived_id("invoices", company_id, ref),
                company_id=company_id,
                customer_id=customer_id,
                invoice_ref=ref,
                issued_date=BOOK_DATE,
                due_date=date(2026, 8, 31),
                amount_minor=amount,
                open_amount_minor=amount - paid,
                currency=CURRENCY,
                status="part_paid" if paid else "open",
                effective_at=AS_OF,
                recorded_at=AS_OF,
                **common,
                **_src("invoices", ref),
            )
        )
        session.add(log.emit("invoice_issued", "invoices", ref, {"amount_minor": amount}, AS_OF))
    session.flush()

    # Cash actually moves: a partial receipt against INV-1001.
    payment_id = derived_id("payments", company_id, PARTIAL_PAYMENT_REF)
    session.add(
        Payment(
            id=payment_id,
            company_id=company_id,
            payment_ref=PARTIAL_PAYMENT_REF,
            amount_minor=PARTIAL_PAYMENT_MINOR,
            currency=CURRENCY,
            paid_date=PAY_DATE,
            method="ach",
            channel="bank",
            effective_at=AS_OF,
            recorded_at=AS_OF,
            **common,
            **_src("payments", PARTIAL_PAYMENT_REF),
        )
    )
    session.add(
        log.emit(
            "payment_received",
            "payments",
            PARTIAL_PAYMENT_REF,
            {"amount_minor": PARTIAL_PAYMENT_MINOR},
            AS_OF,
        )
    )
    session.flush()
    session.add(
        PaymentApplication(
            id=derived_id("payment_applications", payment_id, PARTIAL_PAYMENT_INVOICE),
            payment_id=payment_id,
            invoice_id=derived_id("invoices", company_id, PARTIAL_PAYMENT_INVOICE),
            amount_minor=PARTIAL_PAYMENT_MINOR,
            currency=CURRENCY,
            applied_date=PAY_DATE,
            effective_at=AS_OF,
            recorded_at=AS_OF,
            **common,
            **_src("payment_applications", f"{PARTIAL_PAYMENT_REF}-{PARTIAL_PAYMENT_INVOICE}"),
        )
    )
    session.add(
        log.emit(
            "invoice_payment_applied",
            "payment_applications",
            f"{PARTIAL_PAYMENT_REF}-{PARTIAL_PAYMENT_INVOICE}",
            {"amount_minor": PARTIAL_PAYMENT_MINOR},
            AS_OF,
        )
    )

    session.add(
        BankTransaction(
            id=derived_id("bank_transactions", bank_id, "open-1"),
            account_id=bank_id,
            booking_date=BOOK_DATE,
            value_date=BOOK_DATE,
            amount_minor=OPENING_CASH_MINOR,
            currency=CURRENCY,
            pending=False,
            counterparty="Opening balance",
            description="seeded opening cash",
            category="other",
            effective_at=AS_OF,
            recorded_at=AS_OF,
            **common,
            **_src("bank_transactions", "open-1"),
        )
    )
    session.add(
        BankTransaction(
            id=derived_id("bank_transactions", bank_id, PARTIAL_PAYMENT_REF),
            account_id=bank_id,
            booking_date=PAY_DATE,
            value_date=PAY_DATE,
            amount_minor=PARTIAL_PAYMENT_MINOR,
            currency=CURRENCY,
            pending=False,
            counterparty="Customer A",
            description=f"receipt {PARTIAL_PAYMENT_REF}",
            category="receipts_trade_ar",
            effective_at=AS_OF,
            recorded_at=AS_OF,
            **common,
            **_src("bank_transactions", PARTIAL_PAYMENT_REF),
        )
    )
    for pk in ("open-1", PARTIAL_PAYMENT_REF):
        session.add(log.emit("bank_transaction_posted", "bank_transactions", pk, {}, AS_OF))

    post_journal(
        "JE-0003",
        PAY_DATE,
        f"cash receipt {PARTIAL_PAYMENT_REF}",
        (
            ("cash_operating", PARTIAL_PAYMENT_MINOR, 0),
            ("ar_trade", 0, PARTIAL_PAYMENT_MINOR),
        ),
    )

    session.commit()
    return company_id


def main() -> None:
    """`make seed-trivial` entry point: seed a migrated database and verify it."""
    import os

    from sqlalchemy import create_engine

    from backend.models.invariants import assert_consistent

    url = os.environ.get("DATABASE_URL", "sqlite:///novatech_trivial.db")
    engine = create_engine(url)
    try:
        with Session(engine) as session:
            company_id = seed_trivial(session)
            assert_consistent(session)
        print(f"seeded {company_id} into {url}; all invariants hold")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
