from __future__ import annotations

from pathlib import Path

from backend.ingest import (
    BalanceSnapshot,
    MappedTotals,
    check_existing_mapping_drift,
    fingerprint_from_tables,
    infer_format_signature,
    match_template,
    onboard_csv,
    reconcile,
)


def test_format_signature_never_needs_values_beyond_shape() -> None:
    assert infer_format_signature(["INV-000001", "INV-000002"]) == r"^INV-\d+$"
    assert infer_format_signature(["USD", "EUR"]) == r"^[A-Z]{3}$"


def test_csv_template_match_needs_zero_model_calls(tmp_path: Path) -> None:
    (tmp_path / "invoices.csv").write_text(
        "invoice_ref,customer,amount_minor,currency,due_date\nINV-1,Acme,10000,USD,2026-09-15\n",
        encoding="utf-8",
    )
    (tmp_path / "bank_transactions.csv").write_text(
        "txn_id,amount_minor,currency,booked_on\nt1,10000,USD,2026-09-01\n",
        encoding="utf-8",
    )
    (tmp_path / "vendor_invoices.csv").write_text(
        "invoice_ref,vendor,amount_minor,currency,due_date\nVIN-1,Supplier,4000,USD,2026-09-20\n",
        encoding="utf-8",
    )
    out = tmp_path / "mappings"
    result = onboard_csv(
        tmp_path,
        stated=BalanceSnapshot(ar_minor=10_000, ap_minor=4_000, cash_minor=10_000),
        mapped=MappedTotals(
            open_invoices_minor=10_000,
            open_vendor_invoices_minor=4_000,
            bank_transactions_minor=10_000,
            mapped_rows=3,
            source_rows=3,
            mapped_value_minor=24_000,
            source_value_minor=24_000,
        ),
        output_dir=out,
    )
    assert result.template is not None
    assert result.template.template_id == "csv_upload"
    assert result.model_calls == 0
    assert result.reconciliation is not None
    assert result.reconciliation.accepted is True
    assert result.mapping_path is not None
    assert result.mapping_path.exists()


def test_reconciliation_gate_rejects_ar_mismatch() -> None:
    from backend.ingest.mapping import draft_from_template

    mapping = draft_from_template(
        template_id="csv_upload",
        source="csv",
        fingerprint_hash="abc",
    )
    result = reconcile(
        mapping,
        stated=BalanceSnapshot(ar_minor=100, ap_minor=0, cash_minor=0),
        mapped=MappedTotals(
            open_invoices_minor=90,
            open_vendor_invoices_minor=0,
            bank_transactions_minor=0,
            mapped_rows=1,
            source_rows=1,
            mapped_value_minor=90,
            source_value_minor=100,
        ),
    )
    assert result.accepted is False
    assert any("AR mismatch" in failure for failure in result.failures)


def test_dodo_template_matches_structural_fingerprint() -> None:
    fingerprint = fingerprint_from_tables(
        [
            {
                "name": "payments",
                "columns": [
                    {"name": "payment_id", "data_type": "text"},
                    {"name": "amount", "data_type": "bigint"},
                    {"name": "currency", "data_type": "text"},
                    {"name": "status", "data_type": "text"},
                    {"name": "created_at", "data_type": "timestamptz"},
                ],
            },
            {
                "name": "subscriptions",
                "columns": [
                    {"name": "subscription_id", "data_type": "text"},
                    {"name": "customer_id", "data_type": "text"},
                    {"name": "status", "data_type": "text"},
                    {"name": "next_billing_date", "data_type": "date"},
                ],
            },
            {
                "name": "customers",
                "columns": [
                    {"name": "customer_id", "data_type": "text"},
                    {"name": "email", "data_type": "text"},
                    {"name": "name", "data_type": "text"},
                ],
            },
            {
                "name": "refunds",
                "columns": [
                    {"name": "refund_id", "data_type": "text"},
                    {"name": "payment_id", "data_type": "text"},
                    {"name": "amount", "data_type": "bigint"},
                    {"name": "created_at", "data_type": "timestamptz"},
                ],
            },
            {
                "name": "disputes",
                "columns": [
                    {"name": "dispute_id", "data_type": "text"},
                    {"name": "payment_id", "data_type": "text"},
                    {"name": "amount", "data_type": "bigint"},
                    {"name": "status", "data_type": "text"},
                ],
            },
            {
                "name": "payouts",
                "columns": [
                    {"name": "payout_id", "data_type": "text"},
                    {"name": "amount", "data_type": "bigint"},
                    {"name": "currency", "data_type": "text"},
                    {"name": "status", "data_type": "text"},
                ],
            },
        ],
        source_kind="dodo",
    )
    match = match_template(fingerprint)
    assert match is not None
    assert match.template_id == "dodo_saas"


def test_drift_watch_holds_ingestion(tmp_path: Path) -> None:
    (tmp_path / "invoices.csv").write_text(
        "invoice_ref,customer,amount_minor,currency,due_date\n", encoding="utf-8"
    )
    (tmp_path / "bank_transactions.csv").write_text(
        "txn_id,amount_minor,currency,booked_on\n", encoding="utf-8"
    )
    (tmp_path / "vendor_invoices.csv").write_text(
        "invoice_ref,vendor,amount_minor,currency,due_date\n", encoding="utf-8"
    )
    result = onboard_csv(
        tmp_path,
        stated=BalanceSnapshot(ar_minor=0, ap_minor=0, cash_minor=0),
        mapped=MappedTotals(
            open_invoices_minor=0,
            open_vendor_invoices_minor=0,
            bank_transactions_minor=0,
            mapped_rows=0,
            source_rows=0,
            mapped_value_minor=0,
            source_value_minor=0,
        ),
        output_dir=tmp_path / "out",
    )
    assert result.mapping is not None
    drifted = fingerprint_from_tables(
        [{"name": "invoices", "columns": [{"name": "unexpected", "data_type": "text"}]}],
        source_kind="csv",
        dialect="csv",
    )
    report = check_existing_mapping_drift(result.mapping, drifted)
    assert report.changed is True
    assert report.hold_ingestion is True
