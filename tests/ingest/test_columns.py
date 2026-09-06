"""The deterministic Cartographer: does the lexicon read a schema it has never seen?

These tests are written against the *shapes* real source systems come in rather than
against the demo databases, so a test passing does not depend on the demo generator
having been run. `test_demo_schemas_map_completely` is the one that does, and it is the
end-to-end claim: four naming conventions, six entities, every field found.
"""

from __future__ import annotations

import pytest

from backend.ingest.columns import ACCEPT_BPS, classify_table, map_columns, propose, tokens
from backend.ingest.fingerprint import fingerprint_from_tables


def profile(name: str, columns: list[tuple[str, str]], rows: int = 100) -> dict[str, object]:
    return {
        "name": name,
        "row_count": rows,
        "columns": [{"name": column, "data_type": kind} for column, kind in columns],
        "foreign_keys": [],
    }


ERP_AR = profile(
    "ar_open_items",
    [
        ("doc_no", "varchar(24)"),
        ("cust_code", "varchar(16)"),
        ("doc_dt", "date"),
        ("due_dt", "date"),
        ("gross_amt", "decimal(18, 2)"),
        ("open_amt", "decimal(18, 2)"),
        ("curr", "varchar(3)"),
        ("status", "varchar(16)"),
    ],
)

CAMEL_AR = profile(
    "openReceivables",
    [
        ("documentNo", "varchar(32)"),
        ("customerCode", "varchar(24)"),
        ("issueDate", "date"),
        ("dueDate", "date"),
        ("grossAmount", "decimal(18, 2)"),
        ("openAmount", "decimal(18, 2)"),
        ("currencyCode", "varchar(3)"),
        ("status", "varchar(16)"),
    ],
)

LEGACY_AR = profile(
    "receivable_ledger",
    [
        ("document_no", "varchar(32)"),
        ("client_code", "varchar(24)"),
        ("posted_on", "date"),
        ("date_due", "date"),
        ("amount_cents", "bigint"),
        ("open_amt_cents", "bigint"),
        ("ccy", "varchar(3)"),
        ("doc_status", "varchar(16)"),
    ],
)


class TestTokens:
    def test_camel_case_boundaries_survive_lowercasing(self) -> None:
        # The bug this guards: lowercase first and `customerMaster` becomes one token.
        assert tokens("customerMaster") == {"customer", "master"}

    def test_synonyms_collapse_spellings_of_one_idea(self) -> None:
        assert tokens("cust_code") == tokens("client_code") == tokens("customerId")

    def test_unit_suffixes_are_not_part_of_the_field_name(self) -> None:
        assert tokens("amount_cents") == tokens("amount") == {"amount"}


class TestClassification:
    @pytest.mark.parametrize("table", [ERP_AR, CAMEL_AR, LEGACY_AR], ids=["erp", "camel", "legacy"])
    def test_receivables_are_recognised_in_three_naming_conventions(
        self, table: dict[str, object]
    ) -> None:
        fingerprint = fingerprint_from_tables([table], source_kind="database")
        verdict = classify_table(fingerprint.tables[0])
        assert verdict is not None
        role, confidence = verdict
        assert role == "Invoice"
        assert confidence >= ACCEPT_BPS

    def test_a_payables_table_is_not_read_as_receivables(self) -> None:
        """The side hint doing its job: the counterparty column decides the ledger side."""
        payables = profile(
            "ap_open_items",
            [
                ("doc_no", "varchar(24)"),
                ("supp_code", "varchar(16)"),
                ("due_dt", "date"),
                ("gross_amt", "decimal(18, 2)"),
                ("curr", "varchar(3)"),
            ],
        )
        fingerprint = fingerprint_from_tables([payables], source_kind="database")
        verdict = classify_table(fingerprint.tables[0])
        assert verdict is not None
        assert verdict[0] == "VendorInvoice"

    def test_a_table_that_is_none_of_our_entities_is_left_alone(self) -> None:
        audit = profile(
            "sys_audit_trail",
            [("event_id", "varchar(32)"), ("actor", "varchar(64)"), ("payload", "text")],
        )
        fingerprint = fingerprint_from_tables([audit], source_kind="database")
        assert classify_table(fingerprint.tables[0]) is None


class TestColumnMapping:
    def test_every_field_of_an_erp_receivable_is_found(self) -> None:
        fingerprint = fingerprint_from_tables([ERP_AR], source_kind="database")
        mapped = {
            row.canonical_field: row.column for row in map_columns(fingerprint.tables[0], "Invoice")
        }
        assert mapped == {
            "external_id": "doc_no",
            "customer_ref": "cust_code",
            "issued_date": "doc_dt",
            "due_date": "due_dt",
            "amount": "gross_amt",
            "open_amount": "open_amt",
            "currency": "curr",
            "status": "status",
        }

    def test_a_column_is_claimed_by_one_field_only(self) -> None:
        """`customer_id` must not also be read as the invoice's own reference."""
        fingerprint = fingerprint_from_tables([ERP_AR], source_kind="database")
        mapped = map_columns(fingerprint.tables[0], "Invoice")
        columns = [row.column for row in mapped]
        assert len(columns) == len(set(columns))

    def test_a_wrongly_typed_column_is_disqualified_not_merely_discouraged(self) -> None:
        """A `due_date` read from a boolean is a mis-map that fails much later, loudly."""
        table = profile(
            "invoices",
            [
                ("invoice_no", "varchar(32)"),
                ("customer_id", "varchar(24)"),
                ("due_date", "boolean"),
                ("amount", "decimal(18, 2)"),
                ("currency", "varchar(3)"),
            ],
        )
        fingerprint = fingerprint_from_tables([table], source_kind="database")
        mapped = {row.canonical_field for row in map_columns(fingerprint.tables[0], "Invoice")}
        assert "due_date" not in mapped

    def test_integer_and_named_minor_units_are_read_as_minor(self) -> None:
        fingerprint = fingerprint_from_tables([LEGACY_AR], source_kind="database")
        proposal, _ = propose(fingerprint)
        units = {row.column: row.units for row in proposal.units}
        assert units == {"amount_cents": "minor", "open_amt_cents": "minor"}

    def test_decimal_columns_are_read_as_major_units(self) -> None:
        fingerprint = fingerprint_from_tables([ERP_AR], source_kind="database")
        proposal, _ = propose(fingerprint)
        assert {row.units for row in proposal.units} == {"major"}


class TestProposal:
    def test_two_tables_claiming_one_role_leaves_the_loser_as_residue(self) -> None:
        """A question for a human, not a coin toss the loader would have to live with."""
        fingerprint = fingerprint_from_tables(
            [ERP_AR, {**LEGACY_AR, "name": "receivable_ledger"}], source_kind="database"
        )
        proposal, residue = propose(fingerprint)
        invoice_tables = [row.table for row in proposal.tables if row.entity_role == "Invoice"]
        assert len(invoice_tables) == 1
        assert set(residue.tables) == {"ar_open_items", "receivable_ledger"} - set(invoice_tables)

    def test_a_missing_field_is_reported_as_residue_not_silently_dropped(self) -> None:
        table = profile(
            "invoices",
            [
                ("invoice_no", "varchar(32)"),
                ("customer_id", "varchar(24)"),
                ("due_date", "date"),
                ("amount_due", "decimal(18, 2)"),
            ],
        )
        fingerprint = fingerprint_from_tables([table], source_kind="database")
        _, residue = propose(fingerprint)
        assert ("invoices", "currency") in residue.columns
