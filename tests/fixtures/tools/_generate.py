"""Regenerates the tool fixtures in this directory.

These are placeholders in the shape of the real contract, kept here so the runtime and the
six agents are buildable before Person 1's engine lands. When their seeded data arrives,
replace the JSON -- the payload models are the part that must not move.

    python tests/fixtures/tools/_generate.py
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent


def m(major: float, currency: str = "USD") -> dict[str, object]:
    return {"minor_units": round(major * 100), "currency": currency}


WEEK_ENDINGS = [
    "2026-03-06",
    "2026-03-13",
    "2026-03-20",
    "2026-03-27",
    "2026-04-03",
    "2026-04-10",
    "2026-04-17",
    "2026-04-24",
    "2026-05-01",
    "2026-05-08",
    "2026-05-15",
    "2026-05-22",
    "2026-05-29",
]
CLOSING = [
    24_300_000,
    23_400_000,
    21_800_000,
    21_000_000,
    18_800_000,
    18_400_000,
    19_200_000,
    20_300_000,
    20_900_000,
    22_400_000,
    22_200_000,
    23_900_000,
    24_800_000,
]
FLOOR = 20_000_000

files: dict[str, object] = {}

files["get_liquidity_position"] = {
    "as_of": "2026-03-02",
    "cash_today": m(24_800_000),
    "floor": m(FLOOR),
    "min_cash": m(18_400_000),
    "min_cash_week": 6,
    "revolver_available": m(8_000_000),
    "revolver_utilization_pct": "42.0",
    "runway_weeks": 19,
    "references": ["bank:balance-2026-03-02", "policy:treasury-policy-v4#min_cash"],
}

files["get_covenant_status"] = {
    "as_of": "2026-03-02",
    "covenants": [
        {
            "covenant_id": "dscr",
            "label": "Debt service coverage ratio",
            "observed_ratio": "1.42",
            "threshold_ratio": "1.25",
            "headroom_pct": "13.6",
            "breached": False,
            "tested_on": "2026-02-28",
            "reference": "debt:covenant-dscr-2026Q1",
        },
        {
            "covenant_id": "revolver-util",
            "label": "Maximum revolver utilization",
            "observed_ratio": "0.42",
            "threshold_ratio": "0.65",
            "headroom_pct": "35.4",
            "breached": False,
            "tested_on": "2026-02-28",
            "reference": "debt:covenant-util-2026Q1",
        },
    ],
    "references": ["debt:covenant-dscr-2026Q1", "debt:covenant-util-2026Q1"],
}

files["get_policy_constraints"] = {
    "as_of": "2026-03-02",
    "constraints": [
        {
            "constraint_id": "min-cash",
            "kind": "min_cash",
            "severity": "hard",
            "description": "Minimum operating cash across the 13-week horizon",
            "money_threshold": m(FLOOR),
            "source_ref": "policy:treasury-policy-v4#min_cash",
        },
        {
            "constraint_id": "protected-payroll",
            "kind": "protected_payment_class",
            "severity": "hard",
            "description": "Payroll is never deferrable",
            "applies_to": "payroll",
            "source_ref": "policy:treasury-policy-v4#protected_classes",
        },
        {
            "constraint_id": "max-supplier-delay",
            "kind": "max_supplier_delay",
            "severity": "soft",
            "description": "Suppliers are not stretched beyond 30 days past terms",
            "days_threshold": 30,
            "source_ref": "policy:treasury-policy-v4#supplier_delay",
        },
        {
            "constraint_id": "max-revolver-util",
            "kind": "max_revolver_utilization",
            "severity": "hard",
            "description": "Revolver utilization stays below the covenant ceiling",
            "ratio_threshold": "0.65",
            "source_ref": "debt:covenant-util-2026Q1",
        },
    ],
    "references": [
        "policy:treasury-policy-v4#min_cash",
        "policy:treasury-policy-v4#protected_classes",
    ],
}

files["get_capability_manifest"] = {
    "as_of": "2026-03-02",
    "available_sources": [
        "bank",
        "gl",
        "ar_ledger",
        "ap_ledger",
        "payroll",
        "tax_calendar",
        "debt",
        "dodo",
        "forecast",
        "policy",
    ],
    "missing_sources": [],
    "notes": {"dodo": "Subscription volume is live; soft/hard decline taxonomy available."},
    "references": [],
}

files["get_forecast_summary"] = {
    "as_of": "2026-03-02",
    "version_id": "fv-2026-W10",
    "published": False,
    "weeks": [
        {
            "week_index": index + 1,
            "week_ending": WEEK_ENDINGS[index],
            "closing_cash": m(closing),
            "breaches_floor": closing < FLOOR,
        }
        for index, closing in enumerate(CLOSING)
    ],
    "references": ["forecast:fv-2026-W10"],
}

files["list_driver_assumptions"] = {
    "as_of": "2026-03-02",
    "rows": [
        {
            "category": "Dodo subscription receipts",
            "driver": "soft-decline recovery rate",
            "value_display": "61% within 14 days",
            "last_refreshed": "2026-02-11",
            "days_since_refresh": 19,
            "stale": True,
            "reference": "dodo:decline-2026-W10#soft_rate",
        },
        {
            "category": "AR collections - enterprise",
            "driver": "30-day collection curve",
            "value_display": "72% of open AR inside terms",
            "last_refreshed": "2026-02-27",
            "days_since_refresh": 3,
            "stale": False,
            "reference": "ar_ledger:curve-2026-W09",
        },
        {
            "category": "Operating expenses",
            "driver": "run-rate from trailing 8 weeks",
            "value_display": "$1.45M per week",
            "last_refreshed": "2026-03-01",
            "days_since_refresh": 1,
            "stale": False,
            "reference": "gl:opex-runrate-2026-W09",
        },
        {
            "category": "AP - vendor payments",
            "driver": "payment-run cadence",
            "value_display": "weekly, Thursday",
            "last_refreshed": "2026-02-24",
            "days_since_refresh": 6,
            "stale": False,
            "reference": "ap_ledger:cadence-2026-W08",
        },
    ],
    "references": [],
}

files["get_variance_bridge"] = {
    "as_of": "2026-03-02",
    "week_ending": "2026-02-27",
    "total_delta": m(-1_870_000),
    "rows": [
        {
            "category": "AR collections - enterprise",
            "plan": m(5_600_000),
            "actual": m(4_200_000),
            "delta": m(-1_400_000),
            "material": True,
            "reference": "ar_ledger:INV-10482#amount_due",
        },
        {
            "category": "AP - vendor payments",
            "plan": m(2_800_000),
            "actual": m(3_100_000),
            "delta": m(-300_000),
            "material": True,
            "reference": "ap_ledger:run-2026-W09",
        },
        {
            "category": "Dodo subscription receipts",
            "plan": m(2_100_000),
            "actual": m(1_900_000),
            "delta": m(-200_000),
            "material": True,
            "reference": "dodo:decline-2026-W10#soft_rate",
        },
        {
            "category": "Operating expenses",
            "plan": m(1_450_000),
            "actual": m(1_420_000),
            "delta": m(30_000),
            "material": False,
            "reference": "gl:opex-2026-W09",
        },
    ],
    "references": [],
}

files["get_forecast_error_percentiles"] = {
    "as_of": "2026-03-02",
    "percentiles": [
        {
            "horizon_weeks": 1,
            "category": "ar_collections",
            "p50_pct": "2.1",
            "p90_pct": "6.4",
            "sample_size": 26,
        },
        {
            "horizon_weeks": 4,
            "category": "ar_collections",
            "p50_pct": "5.8",
            "p90_pct": "12.9",
            "sample_size": 26,
        },
        {
            "horizon_weeks": 6,
            "category": "ar_collections",
            "p50_pct": "8.4",
            "p90_pct": "17.3",
            "sample_size": 26,
        },
        {
            "horizon_weeks": 6,
            "category": "dodo_receipts",
            "p50_pct": "4.2",
            "p90_pct": "9.8",
            "sample_size": 26,
        },
        {
            "horizon_weeks": 13,
            "category": "ar_collections",
            "p50_pct": "14.2",
            "p90_pct": "26.1",
            "sample_size": 26,
        },
    ],
    "references": ["forecast:accuracy-2026-W10"],
}

COLLECTIONS = [
    (
        "Contoso Ltd",
        "INV-10482",
        1_200_000,
        "2026-01-20",
        41,
        "38.0",
        456_000,
        "41 days past terms, no commitment on file; this bucket settles 38% historically",
    ),
    (
        "Fabrikam Inc",
        "INV-10517",
        880_000,
        "2026-02-13",
        17,
        "64.0",
        563_200,
        "inside the 30-day bucket, 64% collected on a call",
    ),
    (
        "Northwind Traders",
        "INV-10466",
        640_000,
        "2026-01-09",
        52,
        "22.0",
        140_800,
        "disputed delivery; 22% settles without escalation",
    ),
    (
        "Tailspin Systems",
        "INV-10530",
        410_000,
        "2026-02-24",
        6,
        "81.0",
        332_100,
        "current, high-frequency payer",
    ),
    (
        "Adventure Works",
        "INV-10493",
        350_000,
        "2026-02-02",
        28,
        "47.0",
        164_500,
        "partial payment plan proposed last quarter",
    ),
]

files["rank_collection_opportunities"] = {
    "as_of": "2026-03-02",
    "total_open": m(sum(row[2] for row in COLLECTIONS)),
    "total_expected": m(sum(row[6] for row in COLLECTIONS)),
    "rows": [
        {
            "customer": row[0],
            "document_ref": row[1],
            "amount": m(row[2]),
            "due_date": row[3],
            "days_past_due": row[4],
            "probability_pct": row[5],
            "expected_amount": m(row[6]),
            "empirical_basis": row[7],
            "reference": f"ar_ledger:{row[1]}#amount_due",
        }
        for row in COLLECTIONS
    ],
    "references": [],
}

files["get_ar_aging_summary"] = {
    "as_of": "2026-03-02",
    "total": m(8_420_000),
    "buckets": [
        {"label": "Current", "amount": m(4_120_000), "invoice_count": 38},
        {"label": "1-30 days", "amount": m(2_180_000), "invoice_count": 17},
        {"label": "31-60 days", "amount": m(1_480_000), "invoice_count": 9},
        {"label": "60+ days", "amount": m(640_000), "invoice_count": 4},
    ],
    "references": ["ar_ledger:aging-2026-W10"],
}

DEFERRALS = [
    ("Acme Components", "BILL-8841", 1_100_000, "2026-03-19", 21, "trade", False, 22_000),
    ("Globex Logistics", "BILL-8863", 620_000, "2026-03-26", 30, "trade", False, None),
    ("Initech Software", "BILL-8877", 340_000, "2026-04-02", 14, "trade", False, 6_800),
    ("NovaTech Payroll", "PAY-2026-W12", 2_450_000, "2026-03-20", 0, "payroll", True, None),
    ("State Revenue Office", "TAX-2026-Q1", 850_000, "2026-03-27", 0, "tax", True, None),
]

files["rank_deferral_candidates"] = {
    "as_of": "2026-03-02",
    "total_deferrable": m(sum(row[2] for row in DEFERRALS if not row[6])),
    "rows": [
        {
            "supplier": row[0],
            "document_ref": row[1],
            "amount": m(row[2]),
            "due_date": row[3],
            "max_delay_days": row[4],
            "payment_class": row[5],
            "protected": row[6],
            **({"discount_forgone": m(row[7])} if row[7] else {}),
            "reference": f"ap_ledger:{row[1]}#due_date",
        }
        for row in DEFERRALS
    ],
    "references": [],
}

files["get_supplier_risk_profile"] = {
    "Acme Components": {
        "as_of": "2026-03-02",
        "supplier": "Acme Components",
        "sole_source": True,
        "concentration_pct": "31.0",
        "late_payments_6m": 3,
        "open_disputes": 1,
        "contractual_terms_days": 30,
        "on_credit_hold": False,
        "notes": "Sole source for the controller board; a further stretch risks allocation.",
        "references": ["ap_ledger:supplier-acme#risk"],
    },
    "Globex Logistics": {
        "as_of": "2026-03-02",
        "supplier": "Globex Logistics",
        "sole_source": False,
        "concentration_pct": "8.0",
        "late_payments_6m": 0,
        "open_disputes": 0,
        "contractual_terms_days": 45,
        "on_credit_hold": False,
        "notes": "Two qualified alternates; tolerant of 30-day stretches historically.",
        "references": ["ap_ledger:supplier-globex#risk"],
    },
}

DECLINES = [
    ("insufficient_funds", "soft", 412, 620_000, 378_200, 14),
    ("expired_card", "soft", 168, 240_000, 158_400, 21),
    ("do_not_honor", "soft", 96, 148_000, 61_000, 7),
    ("stolen_card", "hard", 21, 32_000, 0, None),
    ("closed_account", "hard", 44, 68_000, 0, None),
]

files["get_dodo_decline_breakdown"] = {
    "as_of": "2026-03-02",
    "at_risk_total": m(sum(row[3] for row in DECLINES)),
    "recoverable_total": m(sum(row[4] for row in DECLINES)),
    "rows": [
        {
            "code": row[0],
            "kind": row[1],
            "count": row[2],
            "amount": m(row[3]),
            "recoverable_amount": m(row[4]),
            **({"retry_window_days": row[5]} if row[5] else {}),
            "reference": f"dodo:decline-2026-W10#{row[0]}",
        }
        for row in DECLINES
    ],
    "references": [],
}

evidence: dict[str, object] = {}


def ev(reference: str, source: str, excerpt: str, **fields: str) -> None:
    evidence[reference] = {
        "reference": reference,
        "source": source,
        "excerpt": excerpt,
        "fields": {key: str(value) for key, value in fields.items()},
        "resolved": True,
        "as_of": "2026-03-02",
        "references": [reference],
    }


ev(
    "ar_ledger:INV-10482#amount_due",
    "ar_ledger",
    "Contoso Ltd, invoice 10482, $1.2M, due 2026-01-20, 41 days past terms",
    customer="Contoso Ltd",
    amount_due="1200000.00 USD",
    due_date="2026-01-20",
    dispute="delivery note",
)
ev(
    "ar_ledger:aging-2026-W10",
    "ar_ledger",
    "AR aging at 2026-03-02: $8.42M open across 68 invoices",
    total="8420000.00 USD",
    # `derived_from` is how the Evidence Explorer walks an aggregate down to the rows it
    # was computed from. It lives in `fields` because the payload contract has no
    # provenance edge yet -- that is a shared-contract change to agree at Merge Point 1.
    derived_from=", ".join(
        [
            "ar_ledger:INV-10482#amount_due",
            "ar_ledger:INV-10517#amount_due",
            "ar_ledger:INV-10466#amount_due",
            "ar_ledger:INV-10530#amount_due",
            "ar_ledger:INV-10493#amount_due",
        ]
    ),
)
ev(
    "ar_ledger:curve-2026-W09",
    "ar_ledger",
    "Empirical 30-day collection curve: 72% of open AR settles inside terms",
)
ev(
    "ap_ledger:BILL-8841#due_date",
    "ap_ledger",
    "Acme Components, bill 8841, $1.1M, due 2026-03-19, 2/10 net 30",
    supplier="Acme Components",
    amount="1100000.00 USD",
    discount_forgone="22000.00 USD",
)
ev(
    "ap_ledger:supplier-acme#risk",
    "ap_ledger",
    "Acme Components: sole source, 31% of category spend, 3 late payments in 6 months",
)
ev(
    "ap_ledger:run-2026-W09",
    "ap_ledger",
    "Payment run 2026-W09 executed two days early to capture a 2/10 discount worth $58K",
)
# Schedule rows behind the forecast lines that are not a ledger balance: payroll, the tax
# calendar, the debt schedule. Without these, a cell on the Forecast screen would show a
# number with nothing underneath it, which is the one thing that screen must not do.
ev(
    "payroll:schedule-2026-Q1",
    "payroll",
    "Payroll schedule 2026-Q1: fortnightly, $2.40M rising to $2.50M from W11",
    cadence="fortnightly",
    next_run="2026-03-06",
)
ev(
    "tax_calendar:2026-Q1",
    "tax_calendar",
    "Statutory tax calendar 2026-Q1: $850K due 2026-03-27, $900K due 2026-04-24",
)
ev(
    "debt:schedule-2026",
    "debt",
    "Debt service schedule: $640K every four weeks against the 2026 term facility",
)
ev(
    "gl:other-receipts-2026-W10",
    "gl",
    "Other receipts run-rate: $180K-$320K per week, trailing 8 weeks",
)
ev(
    "gl:accuracy-note-2026-W10",
    "gl",
    "Forecast accuracy roll-forward published with the W10 close",
)
ev(
    "dodo:decline-2026-W10#soft_rate",
    "dodo",
    "Soft declines 8.4% of the renewal cohort, up 3.1pp week on week; $597.6K recoverable "
    "inside documented retry windows",
)
ev(
    "policy:treasury-policy-v4#min_cash",
    "policy",
    "TreasuryPolicy v4: minimum operating cash $20.0M, hard constraint",
)
ev(
    "policy:treasury-policy-v4#protected_classes",
    "policy",
    "TreasuryPolicy v4: payroll and statutory tax are protected payment classes",
)
ev(
    "bank:balance-2026-03-02",
    "bank",
    "Reconciled bank balance at 2026-03-02: $24.8M across four accounts",
)
ev(
    "forecast:fv-2026-W10",
    "forecast",
    "Forecast version fv-2026-W10, draft, minimum cash $18.4M at W6",
    derived_from=", ".join(
        [
            "bank:balance-2026-03-02",
            "ar_ledger:aging-2026-W10",
            "dodo:settlement-2026-W10",
            "ap_ledger:cadence-2026-W08",
            "policy:treasury-policy-v4#min_cash",
        ]
    ),
)
ev(
    "dodo:settlement-2026-W10",
    "dodo",
    "Dodo settlement 2026-W10: $1.90M received against $3.01M billed",
    billed="3010000.00 USD",
    settled="1900000.00 USD",
    derived_from=", ".join(
        [
            "dodo:decline-2026-W10#soft_rate",
            "dodo:decline-2026-W10#insufficient_funds",
            "dodo:decline-2026-W10#expired_card",
        ]
    ),
)
ev(
    "ap_ledger:run-2026-W10",
    "ap_ledger",
    "Payment run 2026-W10 scheduled for 2026-03-05: $2.06M across 41 payables",
    total="2060000.00 USD",
    derived_from=", ".join(
        [
            "ap_ledger:BILL-8841#due_date",
            "ap_ledger:BILL-8863#due_date",
            "ap_ledger:BILL-8877#due_date",
            "ap_ledger:cadence-2026-W08",
        ]
    ),
)

# Every reference any tool returns has to resolve, or an agent that cites a row it was
# handed loses the finding for a fixture gap rather than a fabrication. The rule is:
# nothing above the evidence validator gets to decide which citations are real.
ev(
    "ar_ledger:INV-10517#amount_due",
    "ar_ledger",
    "Fabrikam Inc, invoice 10517, $880K, due 2026-02-13, 17 days past terms",
    customer="Fabrikam Inc",
    amount_due="880000.00 USD",
    due_date="2026-02-13",
)
ev(
    "ar_ledger:INV-10466#amount_due",
    "ar_ledger",
    "Northwind Traders, invoice 10466, $640K, due 2026-01-09, 52 days past terms, disputed",
    customer="Northwind Traders",
    amount_due="640000.00 USD",
    due_date="2026-01-09",
    dispute="delivery shortfall",
)
ev(
    "ar_ledger:INV-10530#amount_due",
    "ar_ledger",
    "Tailspin Systems, invoice 10530, $410K, due 2026-02-24, 6 days past terms",
    customer="Tailspin Systems",
    amount_due="410000.00 USD",
    due_date="2026-02-24",
)
ev(
    "ar_ledger:INV-10493#amount_due",
    "ar_ledger",
    "Adventure Works, invoice 10493, $350K, due 2026-02-02, 28 days past terms",
    customer="Adventure Works",
    amount_due="350000.00 USD",
    due_date="2026-02-02",
    note="partial payment plan proposed last quarter",
)
ev(
    "ap_ledger:BILL-8863#due_date",
    "ap_ledger",
    "Globex Logistics, bill 8863, $620K, due 2026-03-26, net 45",
    supplier="Globex Logistics",
    amount="620000.00 USD",
    due_date="2026-03-26",
)
ev(
    "ap_ledger:BILL-8877#due_date",
    "ap_ledger",
    "Initech Software, bill 8877, $340K, due 2026-04-02, 2/10 net 30",
    supplier="Initech Software",
    amount="340000.00 USD",
    due_date="2026-04-02",
    discount_forgone="6800.00 USD",
)
ev(
    "ap_ledger:PAY-2026-W12#due_date",
    "ap_ledger",
    "Payroll run 2026-W12, $2.45M, due 2026-03-20, protected payment class",
    payment_class="payroll",
    amount="2450000.00 USD",
    due_date="2026-03-20",
    protected="true",
)
ev(
    "ap_ledger:TAX-2026-Q1#due_date",
    "ap_ledger",
    "Statutory tax remittance 2026-Q1, $850K, due 2026-03-27, protected payment class",
    payment_class="tax",
    amount="850000.00 USD",
    due_date="2026-03-27",
    protected="true",
)
ev(
    "ap_ledger:supplier-globex#risk",
    "ap_ledger",
    "Globex Logistics: two qualified alternates, 8% of category spend, "
    "no late payments in 6 months",
)
ev(
    "ap_ledger:cadence-2026-W08",
    "ap_ledger",
    "Payment run cadence: weekly, Thursday, last confirmed 2026-02-24",
)
ev(
    "debt:covenant-dscr-2026Q1",
    "debt",
    "Debt service coverage 1.42x against a 1.25x threshold, tested 2026-02-28, 13.6% headroom",
)
ev(
    "debt:covenant-util-2026Q1",
    "debt",
    "Revolver utilization 0.42 against a 0.65 ceiling, tested 2026-02-28, 35.4% headroom",
)
ev(
    "policy:treasury-policy-v4#supplier_delay",
    "policy",
    "TreasuryPolicy v4: suppliers are not stretched beyond 30 days past terms, soft constraint",
)
ev(
    "gl:opex-2026-W09",
    "gl",
    "Operating expenses 2026-W09: $1.42M actual against $1.45M plan",
)
ev(
    "gl:opex-runrate-2026-W09",
    "gl",
    "Operating expense run-rate from the trailing 8 weeks: $1.45M per week",
)
ev(
    "forecast:accuracy-2026-W10",
    "forecast",
    "Measured 13-week forecast error to 2026-W10: p50 4.1%, p90 11.8% on receipts",
)
for _code, _kind, _count, _amount, _recoverable, _window in [
    ("insufficient_funds", "soft", 412, "620000.00", "378200.00", "14-day retry window"),
    ("expired_card", "soft", 168, "240000.00", "158400.00", "21-day retry window"),
    ("do_not_honor", "soft", 96, "148000.00", "61000.00", "7-day retry window"),
    ("stolen_card", "hard", 21, "32000.00", "0.00", "no retry under Dodo's taxonomy"),
    ("closed_account", "hard", 44, "68000.00", "0.00", "no retry under Dodo's taxonomy"),
]:
    ev(
        f"dodo:decline-2026-W10#{_code}",
        "dodo",
        f"{_code} ({_kind}): {_count} declines, ${_amount} at risk, "
        f"${_recoverable} recoverable, {_window}",
        kind=_kind,
        count=_count,
        amount=f"{_amount} USD",
        recoverable=f"{_recoverable} USD",
    )

files["resolve_evidence"] = evidence


if __name__ == "__main__":
    for name, payload in files.items():
        (ROOT / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(files)} fixtures to {ROOT}")
