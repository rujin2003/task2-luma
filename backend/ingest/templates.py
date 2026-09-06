"""Template library for known schema shapes.

Matching is structural — table-name similarity, column-set overlap, FK topology.
Confirmed templates store column names, types and roles only — never values.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.ingest.fingerprint import SchemaFingerprint


@dataclass(frozen=True, slots=True)
class TemplateMatch:
    template_id: str
    score_bps: int
    mapping_path: str
    residual_tables: tuple[str, ...]


#: Structural templates. Scores are basis points of column-name overlap.
TEMPLATES: dict[str, dict[str, object]] = {
    "stripe_quickbooks": {
        "tables": {
            "customers": {"id", "name", "email"},
            "invoices": {"id", "customer_id", "amount_due", "currency", "due_date", "status"},
            "payments": {"id", "invoice_id", "amount", "currency", "created"},
            "vendors": {"id", "display_name"},
            "bills": {"id", "vendor_id", "amount", "currency", "due_date"},
            "accounts": {"id", "name", "account_type", "current_balance"},
        },
        "mapping_path": "templates/stripe_quickbooks.v1.yaml",
    },
    "dodo_saas": {
        "tables": {
            "customers": {"customer_id", "email", "name"},
            "subscriptions": {"subscription_id", "customer_id", "status", "next_billing_date"},
            "payments": {"payment_id", "amount", "currency", "status", "created_at"},
            "refunds": {"refund_id", "payment_id", "amount", "created_at"},
            "disputes": {"dispute_id", "payment_id", "amount", "status"},
            "payouts": {"payout_id", "amount", "currency", "status"},
        },
        "mapping_path": "templates/dodo_saas.v1.yaml",
    },
    "csv_upload": {
        "tables": {
            "invoices": {"invoice_ref", "customer", "amount_minor", "currency", "due_date"},
            "bank_transactions": {"txn_id", "amount_minor", "currency", "booked_on"},
            "vendor_invoices": {"invoice_ref", "vendor", "amount_minor", "currency", "due_date"},
        },
        "mapping_path": "templates/csv_upload.v1.yaml",
    },
}

MATCH_THRESHOLD_BPS = 7_500


def match_template(fingerprint: SchemaFingerprint) -> TemplateMatch | None:
    """Return the best template above threshold, or None for a novel schema."""
    source_tables = {
        t.name.lower(): {c.name.lower() for c in t.columns} for t in fingerprint.tables
    }
    best: TemplateMatch | None = None
    for template_id, spec in TEMPLATES.items():
        expected = spec["tables"]
        assert isinstance(expected, dict)
        scores: list[int] = []
        residual: list[str] = []
        for table_name, columns in expected.items():
            assert isinstance(columns, set)
            actual = source_tables.get(table_name.lower())
            if actual is None:
                residual.append(table_name)
                scores.append(0)
                continue
            if not columns:
                scores.append(10_000)
                continue
            overlap = len(columns & actual)
            scores.append((10_000 * overlap) // len(columns))
        if not scores:
            continue
        score = sum(scores) // len(scores)
        mapping_path = str(spec["mapping_path"])
        candidate = TemplateMatch(
            template_id=template_id,
            score_bps=score,
            mapping_path=mapping_path,
            residual_tables=tuple(sorted(residual)),
        )
        if score >= MATCH_THRESHOLD_BPS and (best is None or score > best.score_bps):
            best = candidate
    return best


__all__ = ["MATCH_THRESHOLD_BPS", "TEMPLATES", "TemplateMatch", "match_template"]
