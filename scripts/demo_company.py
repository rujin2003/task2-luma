"""Build the demo tenants' *source* databases — the ones the DB Agent is pointed at.

None of these are WAR ROOM's schema. They are deliberately somebody else's, and they
are deliberately four different somebodies, because a demo where the agent recognises
a shape we shipped ourselves proves nothing about a schema a customer would paste a
URL to. The four styles are the four ways real systems name things:

* **Helios Robotics** — SAP-flavoured ERP: `cust_master`, `ar_open_items`, `gross_amt`,
  `due_dt`, statuses in the tenant's own words, amounts in major units.
* **Lumen Health Systems** — Stripe/QuickBooks-flavoured: `customers`, `invoices`,
  `bills`, `accounts`, `amount_due`, `due_date`. The friendly case.
* **Northgate Freight** — legacy warehouse: `client_dim`, `receivable_ledger`,
  `cash_movements`, and amounts as **integer minor units** (`amount_cents`). This is
  the schema that punishes a loader that assumes decimals.
* **Aurora Retail Group** — camelCase application DB: `customerMaster`,
  `openReceivables`, `grossAmount`, `dueDate`, multi-currency (USD/GBP/EUR).

Each carries its own liquidity story, so the war room has something specific to find:
a late anchor customer, a supplier pulling terms forward, a currency obligation.

Every figure is integer cents until it is rendered, and the generators are seeded, so
running this twice produces byte-identical rows. `control_balances` is each tenant's
own trial-balance extract, computed from the rows rather than asserted alongside them
— so a generator bug fails the reconciliation gate exactly like a mis-mapping would.
"""

from __future__ import annotations

import argparse
import json
import os
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from scripts.console import use_utf8_stdout

AS_OF = date(2026, 3, 2)
MANIFEST = Path("var/demo-companies.json")


# --- how a source database names and types things ----------------------------------------


@dataclass(frozen=True, slots=True)
class Style:
    """One source system's naming and typing conventions."""

    label: str
    tables: dict[str, str]
    columns: dict[str, dict[str, str]]
    types: dict[str, dict[str, str]]
    units: str  # major | minor
    controls_table: str
    controls_columns: tuple[str, str, str, str, str]


def _erp_style() -> Style:
    return Style(
        label="SAP-flavoured ERP",
        tables={
            "Customer": "cust_master",
            "Vendor": "supplier_master",
            "Invoice": "ar_open_items",
            "VendorInvoice": "ap_open_items",
            "BankAccount": "bank_accounts",
            "BankTransaction": "bank_ledger",
        },
        columns={
            "Customer": {
                "external_id": "cust_code",
                "name": "cust_name",
                "email": "email",
                "country": "country",
                "segment": "segment",
            },
            "Vendor": {
                "external_id": "supp_code",
                "name": "supp_name",
                "criticality": "criticality",
                "category": "category",
            },
            "Invoice": {
                "external_id": "doc_no",
                "customer_ref": "cust_code",
                "issued_date": "doc_dt",
                "due_date": "due_dt",
                "amount": "gross_amt",
                "open_amount": "open_amt",
                "currency": "curr",
                "status": "status",
            },
            "VendorInvoice": {
                "external_id": "doc_no",
                "vendor_ref": "supp_code",
                "issued_date": "doc_dt",
                "due_date": "due_dt",
                "amount": "gross_amt",
                "open_amount": "open_amt",
                "currency": "curr",
                "status": "status",
            },
            "BankAccount": {
                "external_id": "acct_no",
                "name": "bank_name",
                "currency": "curr",
                "balance": "balance",
                "kind": "acct_type",
            },
            "BankTransaction": {
                "external_id": "entry_id",
                "account_ref": "acct_no",
                "booked_on": "value_dt",
                "amount": "amt",
                "currency": "curr",
                "description": "narrative",
            },
        },
        types=_types("DECIMAL(18,2)"),
        units="major",
        controls_table="gl_control_balances",
        controls_columns=("as_of", "ar_control", "ap_control", "cash_control", "curr"),
    )


def _saas_style() -> Style:
    return Style(
        label="Stripe / QuickBooks export",
        tables={
            "Customer": "customers",
            "Vendor": "vendors",
            "Invoice": "invoices",
            "VendorInvoice": "bills",
            "BankAccount": "accounts",
            "BankTransaction": "bank_transactions",
        },
        columns={
            "Customer": {
                "external_id": "customer_id",
                "name": "name",
                "email": "email",
                "country": "country",
                "segment": "segment",
            },
            "Vendor": {
                "external_id": "vendor_id",
                "name": "display_name",
                "criticality": "tier",
                "category": "category",
            },
            "Invoice": {
                "external_id": "invoice_number",
                "customer_ref": "customer_id",
                "issued_date": "invoice_date",
                "due_date": "due_date",
                "amount": "amount_due",
                "open_amount": "balance",
                "currency": "currency",
                "status": "status",
            },
            "VendorInvoice": {
                "external_id": "bill_no",
                "vendor_ref": "vendor_id",
                "issued_date": "bill_date",
                "due_date": "due_date",
                "amount": "amount_due",
                "open_amount": "balance",
                "currency": "currency",
                "status": "status",
            },
            "BankAccount": {
                "external_id": "account_number",
                "name": "account_name",
                "currency": "currency",
                "balance": "current_balance",
                "kind": "account_type",
            },
            "BankTransaction": {
                "external_id": "transaction_id",
                "account_ref": "account_number",
                "booked_on": "posted_on",
                "amount": "amount",
                "currency": "currency",
                "description": "memo",
            },
        },
        types=_types("DECIMAL(18,2)"),
        units="major",
        controls_table="control_balances",
        controls_columns=("as_of", "ar_control", "ap_control", "cash_control", "currency"),
    )


def _legacy_style() -> Style:
    return Style(
        label="legacy warehouse, integer minor units",
        tables={
            "Customer": "client_dim",
            "Vendor": "supplier_dim",
            "Invoice": "receivable_ledger",
            "VendorInvoice": "payable_ledger",
            "BankAccount": "cash_accounts",
            "BankTransaction": "cash_movements",
        },
        columns={
            "Customer": {
                "external_id": "client_code",
                "name": "client_name",
                "email": "contact_email",
                "country": "country_code",
                "segment": "segment",
            },
            "Vendor": {
                "external_id": "supplier_code",
                "name": "supplier_name",
                "criticality": "risk_tier",
                "category": "category",
            },
            "Invoice": {
                "external_id": "document_no",
                "customer_ref": "client_code",
                "issued_date": "posted_on",
                "due_date": "date_due",
                "amount": "amount_cents",
                "open_amount": "open_amt_cents",
                "currency": "ccy",
                "status": "doc_status",
            },
            "VendorInvoice": {
                "external_id": "document_no",
                "vendor_ref": "supplier_code",
                "issued_date": "posted_on",
                "due_date": "date_due",
                "amount": "amount_cents",
                "open_amount": "open_amt_cents",
                "currency": "ccy",
                "status": "doc_status",
            },
            "BankAccount": {
                "external_id": "account_no",
                "name": "account_name",
                "currency": "ccy",
                "balance": "ledger_balance",
                "kind": "account_type",
            },
            "BankTransaction": {
                "external_id": "statement_line",
                "account_ref": "account_no",
                "booked_on": "value_date",
                "amount": "amount_cents",
                "currency": "ccy",
                "description": "details",
            },
        },
        types=_types("BIGINT"),
        units="minor",
        controls_table="control_totals",
        controls_columns=("as_of", "ar_control", "ap_control", "cash_control", "ccy"),
    )


def _camel_style() -> Style:
    return Style(
        label="camelCase application database",
        tables={
            "Customer": "customerMaster",
            "Vendor": "supplierMaster",
            "Invoice": "openReceivables",
            "VendorInvoice": "openPayables",
            "BankAccount": "bankAccounts",
            "BankTransaction": "bankMovements",
        },
        columns={
            "Customer": {
                "external_id": "customerCode",
                "name": "customerName",
                "email": "contactEmail",
                "country": "countryCode",
                "segment": "segment",
            },
            "Vendor": {
                "external_id": "supplierCode",
                "name": "supplierName",
                "criticality": "riskTier",
                "category": "category",
            },
            "Invoice": {
                "external_id": "documentNo",
                "customer_ref": "customerCode",
                "issued_date": "issueDate",
                "due_date": "dueDate",
                "amount": "grossAmount",
                "open_amount": "openAmount",
                "currency": "currencyCode",
                "status": "status",
            },
            "VendorInvoice": {
                "external_id": "documentNo",
                "vendor_ref": "supplierCode",
                "issued_date": "issueDate",
                "due_date": "dueDate",
                "amount": "grossAmount",
                "open_amount": "openAmount",
                "currency": "currencyCode",
                "status": "status",
            },
            "BankAccount": {
                "external_id": "accountNumber",
                "name": "accountName",
                "currency": "currencyCode",
                "balance": "currentBalance",
                "kind": "accountType",
            },
            "BankTransaction": {
                "external_id": "movementId",
                "account_ref": "accountNumber",
                "booked_on": "valueDate",
                "amount": "signedAmount",
                "currency": "currencyCode",
                "description": "narrative",
            },
        },
        types=_types("DECIMAL(18,2)"),
        units="major",
        controls_table="controlBalances",
        controls_columns=("asOf", "arControl", "apControl", "cashControl", "currencyCode"),
    )


def _types(money_type: str) -> dict[str, dict[str, str]]:
    """Column SQL types by canonical field. Money type varies; everything else does not."""
    text_type = "VARCHAR(160)"
    return {
        "Customer": {
            "external_id": "VARCHAR(24)",
            "name": "VARCHAR(128)",
            "email": text_type,
            "country": "VARCHAR(2)",
            "segment": "VARCHAR(32)",
        },
        "Vendor": {
            "external_id": "VARCHAR(24)",
            "name": "VARCHAR(128)",
            "criticality": "VARCHAR(16)",
            "category": "VARCHAR(32)",
        },
        "Invoice": {
            "external_id": "VARCHAR(32)",
            "customer_ref": "VARCHAR(24)",
            "issued_date": "DATE",
            "due_date": "DATE",
            "amount": money_type,
            "open_amount": money_type,
            "currency": "VARCHAR(3)",
            "status": "VARCHAR(16)",
        },
        "VendorInvoice": {
            "external_id": "VARCHAR(32)",
            "vendor_ref": "VARCHAR(24)",
            "issued_date": "DATE",
            "due_date": "DATE",
            "amount": money_type,
            "open_amount": money_type,
            "currency": "VARCHAR(3)",
            "status": "VARCHAR(16)",
        },
        "BankAccount": {
            "external_id": "VARCHAR(32)",
            "name": "VARCHAR(64)",
            "currency": "VARCHAR(3)",
            "balance": money_type,
            "kind": "VARCHAR(24)",
        },
        "BankTransaction": {
            "external_id": "VARCHAR(32)",
            "account_ref": "VARCHAR(32)",
            "booked_on": "DATE",
            "amount": money_type,
            "currency": "VARCHAR(3)",
            "description": text_type,
        },
    }


# --- the tenants -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Tenant:
    key: str
    company: str
    industry: str
    headline: str
    currency: str
    style: Style
    seed: int
    customers: tuple[tuple[str, str, str, str, int], ...]
    suppliers: tuple[tuple[str, str, str, int], ...]
    accounts: tuple[tuple[str, str, str, str, int], ...]
    anchor_late: tuple[str, int, int] | None = None  # customer code, cents, days late
    pulled_forward: tuple[str, int, int] | None = None  # vendor code, cents, days out
    fx_obligation: tuple[str, int, str, int] | None = None  # vendor, cents, ccy, days out
    database: str = ""


HELIOS = Tenant(
    key="helios",
    company="Helios Robotics",
    industry="Industrial automation",
    headline="Anchor customer 22 days late on $4.0M; critical supplier pulled $1.05M forward.",
    currency="USD",
    style=_erp_style(),
    seed=20260302,
    customers=(
        ("C-1001", "Meridian Dynamics", "US", "enterprise", 45),
        ("C-1002", "Arclight Manufacturing", "US", "enterprise", 45),
        ("C-1003", "Kestrel Logistics", "US", "mid_market", 30),
        ("C-1004", "Northwind Foods", "US", "mid_market", 30),
        ("C-1005", "Vantage Aerospace", "US", "enterprise", 60),
        ("C-1006", "Cobalt Pharma", "US", "enterprise", 45),
        ("C-1007", "Ridgeline Mining", "CA", "mid_market", 30),
        ("C-1008", "Delta Grid Utilities", "US", "enterprise", 60),
        ("C-1009", "Harbourview Packaging", "GB", "mid_market", 30),
        ("C-1010", "Solstice Semiconductors", "US", "enterprise", 45),
        ("C-1011", "Ironbark Automotive", "US", "mid_market", 30),
        ("C-1012", "Pinnacle Labs", "US", "smb", 30),
        ("C-1013", "Cascade Textiles", "US", "smb", 30),
        ("C-1014", "Auroral Energy", "CA", "mid_market", 45),
        ("C-1015", "Talon Defence Systems", "US", "enterprise", 60),
        ("C-1016", "Bluepeak Cold Chain", "US", "mid_market", 30),
    ),
    suppliers=(
        ("V-2001", "Kobayashi Actuators", "critical", 45),
        ("V-2002", "Halden Precision Bearings", "critical", 45),
        ("V-2003", "Novemax Controls", "important", 30),
        ("V-2004", "Torvald Steel", "important", 45),
        ("V-2005", "Ellis Freight Partners", "standard", 30),
        ("V-2006", "Quadrant Facilities", "standard", 30),
        ("V-2007", "Brightline Staffing", "important", 15),
        ("V-2008", "Meritas Insurance", "standard", 30),
        ("V-2009", "Orbital Cloud Services", "important", 30),
        ("V-2010", "Vega Industrial Supply", "standard", 30),
        ("V-2011", "Sable Tooling", "standard", 45),
        ("V-2012", "Camden Legal", "standard", 30),
    ),
    accounts=(
        ("8841-0021", "First Meridian Bank", "operating", "USD", 14_100_000_00),
        ("8841-0044", "First Meridian Bank", "collections", "USD", 6_450_000_00),
        ("5520-1180", "Pacific Commerce Bank", "payroll", "USD", 2_400_000_00),
        ("EU-7742-09", "Rheinbank AG", "eur_operating", "EUR", 1_950_000_00),
    ),
    anchor_late=("C-1001", 4_000_000_00, 22),
    pulled_forward=("V-2001", 1_050_000_00, 9),
    fx_obligation=("V-2002", 880_000_00, "EUR", 18),
)

LUMEN = Tenant(
    key="lumen",
    company="Lumen Health Systems",
    industry="Healthcare services",
    headline="Payer mix lengthening; two large claims sit 40+ days past terms.",
    currency="USD",
    style=_saas_style(),
    seed=20260303,
    customers=(
        ("CU-501", "Statewide Health Plan", "US", "enterprise", 60),
        ("CU-502", "Cornerstone Hospitals", "US", "enterprise", 60),
        ("CU-503", "Beacon Clinics Group", "US", "mid_market", 45),
        ("CU-504", "Rivera Family Practice", "US", "smb", 30),
        ("CU-505", "Northlake Surgical", "US", "mid_market", 45),
        ("CU-506", "Unity Care Network", "US", "enterprise", 60),
        ("CU-507", "Prairie Diagnostics", "US", "mid_market", 30),
        ("CU-508", "Halcyon Senior Living", "US", "mid_market", 45),
        ("CU-509", "Summit Occupational Health", "US", "smb", 30),
        ("CU-510", "Westbrook Imaging", "US", "mid_market", 45),
        ("CU-511", "Ashford Dialysis", "US", "mid_market", 45),
        ("CU-512", "Copperfield Pediatrics", "US", "smb", 30),
    ),
    suppliers=(
        ("SU-810", "Medisource Distribution", "critical", 30),
        ("SU-811", "Vitalis Pharmaceuticals", "critical", 45),
        ("SU-812", "Clearview Diagnostics Supply", "important", 30),
        ("SU-813", "Anchor Facilities Group", "standard", 30),
        ("SU-814", "Locum Staffing Partners", "important", 15),
        ("SU-815", "Sentinel Medical Waste", "standard", 30),
        ("SU-816", "Praxis EHR Software", "important", 30),
        ("SU-817", "Bexley Insurance Brokers", "standard", 30),
        ("SU-818", "Copeland Linen Services", "standard", 30),
        ("SU-819", "Redwood Lab Reagents", "important", 45),
    ),
    accounts=(
        ("1140-3390", "Union Federal Bank", "operating", "USD", 9_200_000_00),
        ("1140-3391", "Union Federal Bank", "lockbox", "USD", 4_800_000_00),
        ("2299-0075", "Cascadia Trust", "payroll", "USD", 3_100_000_00),
    ),
    anchor_late=("CU-501", 2_750_000_00, 41),
    pulled_forward=("SU-811", 620_000_00, 12),
)

NORTHGATE = Tenant(
    key="northgate",
    company="Northgate Freight",
    industry="Logistics and haulage",
    headline="Fuel and lease costs front-loaded; receivables concentrated in three shippers.",
    currency="USD",
    style=_legacy_style(),
    seed=20260304,
    customers=(
        ("CL-77", "Continental Grocers", "US", "enterprise", 45),
        ("CL-78", "Atlas Building Supply", "US", "enterprise", 45),
        ("CL-79", "Pacific Rim Importers", "US", "enterprise", 60),
        ("CL-80", "Foxglove Retail", "US", "mid_market", 30),
        ("CL-81", "Granite Materials", "US", "mid_market", 30),
        ("CL-82", "Silverbrook Beverages", "US", "mid_market", 30),
        ("CL-83", "Torrance Auto Parts", "US", "smb", 30),
        ("CL-84", "Highland Paper", "CA", "mid_market", 45),
        ("CL-85", "Beaumont Chemicals", "US", "mid_market", 30),
        ("CL-86", "Larkspur Nurseries", "US", "smb", 30),
    ),
    suppliers=(
        ("SP-310", "Continental Fuel Services", "critical", 15),
        ("SP-311", "Ridgeway Truck Leasing", "critical", 30),
        ("SP-312", "Halloway Tyres", "important", 30),
        ("SP-313", "Interstate Tolling Authority", "critical", 15),
        ("SP-314", "Brightpath Driver Staffing", "important", 15),
        ("SP-315", "Yardmaster Terminals", "standard", 30),
        ("SP-316", "Cobalt Fleet Insurance", "standard", 30),
        ("SP-317", "Dispatchline Software", "standard", 30),
        ("SP-318", "Warrick Maintenance", "important", 30),
    ),
    accounts=(
        ("70-114-882", "Great Lakes Bank", "operating", "USD", 5_600_000_00),
        ("70-114-990", "Great Lakes Bank", "fuel_card", "USD", 1_250_000_00),
        ("41-880-003", "Midtown Savings", "payroll", "USD", 2_050_000_00),
    ),
    anchor_late=("CL-79", 1_880_000_00, 27),
    pulled_forward=("SP-310", 940_000_00, 6),
)

AURORA = Tenant(
    key="aurora",
    company="Aurora Retail Group",
    industry="Multi-country specialty retail",
    headline="GBP and EUR settlement obligations land inside the same 30-day window.",
    currency="USD",
    style=_camel_style(),
    seed=20260305,
    customers=(
        ("AC-9001", "Marchmont Department Stores", "GB", "enterprise", 60),
        ("AC-9002", "Kastel Warenhaus", "DE", "enterprise", 60),
        ("AC-9003", "Belvedere Concessions", "GB", "mid_market", 45),
        ("AC-9004", "Sundial Franchising", "US", "mid_market", 30),
        ("AC-9005", "Petit Nord Boutiques", "FR", "mid_market", 45),
        ("AC-9006", "Crestwood Outlets", "US", "enterprise", 45),
        ("AC-9007", "Havenbrook Malls", "US", "mid_market", 30),
        ("AC-9008", "Lindqvist Retail AB", "SE", "mid_market", 45),
        ("AC-9009", "Tamarind Markets", "US", "smb", 30),
        ("AC-9010", "Orchard Lane Stores", "GB", "mid_market", 45),
        ("AC-9011", "Verity Homeware", "US", "smb", 30),
    ),
    suppliers=(
        ("AS-4401", "Zhenhai Textiles", "critical", 60),
        ("AS-4402", "Lombardi Leatherworks", "important", 45),
        ("AS-4403", "Northern Freight Forwarding", "critical", 30),
        ("AS-4404", "Fairfield Packaging", "standard", 30),
        ("AS-4405", "Meridian Store Fit-Out", "important", 45),
        ("AS-4406", "Blackwell Media Buying", "standard", 30),
        ("AS-4407", "Pinehurst Security", "standard", 30),
        ("AS-4408", "Kilmarnock Logistics", "important", 30),
        ("AS-4409", "Solaris Retail Systems", "important", 30),
    ),
    accounts=(
        ("US-3300-11", "Atlantic National", "operating", "USD", 7_400_000_00),
        ("GB-8890-42", "Thames Commercial", "gbp_operating", "GBP", 2_650_000_00),
        ("EU-5511-07", "Rheinbank AG", "eur_operating", "EUR", 3_150_000_00),
        ("US-3300-77", "Atlantic National", "payroll", "USD", 1_900_000_00),
    ),
    anchor_late=("AC-9002", 2_100_000_00, 19),
    fx_obligation=("AS-4401", 1_640_000_00, "EUR", 14),
)

TENANTS: tuple[Tenant, ...] = (HELIOS, LUMEN, NORTHGATE, AURORA)
BY_KEY = {tenant.key: tenant for tenant in TENANTS}


# --- money, without a float anywhere ------------------------------------------------------


def cents(major_text: str) -> int:
    sign = -1 if major_text.startswith("-") else 1
    whole, _, frac = major_text.lstrip("+-").partition(".")
    return sign * (int(whole or "0") * 100 + int((frac + "00")[:2]))


def major(amount_cents: int) -> str:
    sign = "-" if amount_cents < 0 else ""
    value = abs(amount_cents)
    return f"{sign}{value // 100}.{value % 100:02d}"


def render_money(amount_cents: int, units: str) -> int | str:
    """A minor-unit source stores the integer; a major-unit source stores the decimal."""
    return amount_cents if units == "minor" else major(amount_cents)


# --- row generation -----------------------------------------------------------------------


@dataclass
class Rows:
    """Canonical rows, before a style renames anything."""

    customers: list[dict[str, Any]] = field(default_factory=list)
    suppliers: list[dict[str, Any]] = field(default_factory=list)
    invoices: list[dict[str, Any]] = field(default_factory=list)
    bills: list[dict[str, Any]] = field(default_factory=list)
    accounts: list[dict[str, Any]] = field(default_factory=list)
    movements: list[dict[str, Any]] = field(default_factory=list)
    controls: dict[str, Any] = field(default_factory=dict)


COUNT_BY_SEGMENT = {"enterprise": 6, "mid_market": 5, "smb": 3}
AR_BAND = {
    "enterprise": (180_000, 950_000),
    "mid_market": (40_000, 260_000),
    "smb": (8_000, 60_000),
}
AP_COUNT = {"critical": 6, "important": 5, "standard": 4}
AP_BAND = {
    "critical": (120_000, 620_000),
    "important": (30_000, 210_000),
    "standard": (5_000, 90_000),
}


def build(tenant: Tenant) -> Rows:
    rng = random.Random(tenant.seed)
    rows = Rows()

    rows.customers = [
        {
            "external_id": code,
            "name": name,
            "email": f"ap@{name.split()[0].lower()}.example",
            "country": country,
            "segment": segment,
        }
        for code, name, country, segment, _terms in tenant.customers
    ]
    rows.suppliers = [
        {
            "external_id": code,
            "name": name,
            "criticality": criticality,
            "category": _category(name),
        }
        for code, name, criticality, _terms in tenant.suppliers
    ]

    sequence = 4100
    for code, _name, _country, segment, terms in tenant.customers:
        low, high = AR_BAND[segment]
        for _ in range(COUNT_BY_SEGMENT[segment]):
            sequence += 1
            issued = AS_OF - timedelta(days=rng.randint(5, 95))
            due = issued + timedelta(days=terms)
            gross = rng.randrange(low, high) * 100
            # Old documents mostly settle; the recent book stays open. Without this the
            # aging is flat, and a flat aging is the tell of generated data.
            if (AS_OF - due).days > 45 and rng.random() < 0.75:
                status, open_cents = "PAID", 0
            elif rng.random() < 0.12:
                status, open_cents = "PARTPAID", (gross * rng.randrange(20, 70)) // 100
            else:
                status, open_cents = "OPEN", gross
            rows.invoices.append(
                {
                    "external_id": f"AR-{sequence}",
                    "customer_ref": code,
                    "issued_date": issued.isoformat(),
                    "due_date": due.isoformat(),
                    "amount": gross,
                    "open_amount": open_cents,
                    "currency": tenant.currency,
                    "status": status,
                }
            )

    if tenant.anchor_late is not None:
        code, amount, days_late = tenant.anchor_late
        rows.invoices.append(
            {
                "external_id": "AR-4099",
                "customer_ref": code,
                "issued_date": (AS_OF - timedelta(days=days_late + 45)).isoformat(),
                "due_date": (AS_OF - timedelta(days=days_late)).isoformat(),
                "amount": amount,
                "open_amount": amount,
                "currency": tenant.currency,
                "status": "OPEN",
            }
        )

    sequence = 7300
    for code, _name, criticality, terms in tenant.suppliers:
        low, high = AP_BAND[criticality]
        for _ in range(AP_COUNT[criticality]):
            sequence += 1
            issued = AS_OF - timedelta(days=rng.randint(2, 60))
            due = issued + timedelta(days=terms)
            gross = rng.randrange(low, high) * 100
            if due < AS_OF - timedelta(days=10) and rng.random() < 0.8:
                status, open_cents = "PAID", 0
            else:
                status, open_cents = "OPEN", gross
            rows.bills.append(
                {
                    "external_id": f"AP-{sequence}",
                    "vendor_ref": code,
                    "issued_date": issued.isoformat(),
                    "due_date": due.isoformat(),
                    "amount": gross,
                    "open_amount": open_cents,
                    "currency": tenant.currency,
                    "status": status,
                }
            )

    if tenant.pulled_forward is not None:
        code, amount, days_out = tenant.pulled_forward
        rows.bills.append(
            {
                "external_id": "AP-7299",
                "vendor_ref": code,
                "issued_date": (AS_OF - timedelta(days=6)).isoformat(),
                "due_date": (AS_OF + timedelta(days=days_out)).isoformat(),
                "amount": amount,
                "open_amount": amount,
                "currency": tenant.currency,
                "status": "OPEN",
            }
        )
    if tenant.fx_obligation is not None:
        code, amount, currency, days_out = tenant.fx_obligation
        rows.bills.append(
            {
                "external_id": "AP-7298",
                "vendor_ref": code,
                "issued_date": (AS_OF - timedelta(days=11)).isoformat(),
                "due_date": (AS_OF + timedelta(days=days_out)).isoformat(),
                "amount": amount,
                "open_amount": amount,
                "currency": currency,
                "status": "OPEN",
            }
        )

    # The ledger opens each account with its brought-forward balance and then moves it,
    # so the sum of every movement is cash on hand. That identity is what lets the
    # loader's bank total be reconciled against the trial balance rather than displayed.
    balances = {account[0]: account[4] for account in tenant.accounts}
    for account_no, _bank, _kind, currency, opening in tenant.accounts:
        rows.movements.append(
            {
                "external_id": f"BL-OPEN-{account_no}",
                "account_ref": account_no,
                "booked_on": (AS_OF - timedelta(days=91)).isoformat(),
                "amount": opening,
                "currency": currency,
                "description": "Opening balance carried forward",
            }
        )

    # Split by direction and drawn with an even hand, so ninety days of movements land
    # the closing balance near where it opened. A ledger that quietly drains to nothing
    # would make every tenant look like a crisis before the agents have looked at one.
    inflows = ("Customer receipt", "Card settlement batch", "Lockbox deposit", "Interest credit")
    outflows = (
        "Supplier payment run",
        "Payroll disbursement",
        "FX conversion",
        "Bank fees",
        "Tax remittance",
    )
    entry = 90_000
    for day_offset in range(90, 0, -1):
        moment = AS_OF - timedelta(days=day_offset)
        if moment.weekday() >= 5:
            continue
        for _ in range(rng.randint(2, 5)):
            entry += 1
            account_no, _bank, _kind, currency, _opening = tenant.accounts[
                rng.randrange(len(tenant.accounts))
            ]
            if rng.random() < 0.5:
                label, sign = inflows[rng.randrange(len(inflows))], 1
            else:
                label, sign = outflows[rng.randrange(len(outflows))], -1
            amount = sign * rng.randrange(15_000, 780_000) * 100
            # Never overdraw an account: an implausible ledger invites the audience to
            # argue with the data instead of with the recommendation.
            if balances[account_no] + amount < 250_000_00:
                amount = abs(amount)
            balances[account_no] += amount
            rows.movements.append(
                {
                    "external_id": f"BL-{entry}",
                    "account_ref": account_no,
                    "booked_on": moment.isoformat(),
                    "amount": amount,
                    "currency": currency,
                    "description": f"{label} {moment.isoformat()}",
                }
            )

    rows.accounts = [
        {
            "external_id": account_no,
            "name": bank,
            "kind": kind,
            "currency": currency,
            "balance": balances[account_no],
        }
        for account_no, bank, kind, currency, _opening in tenant.accounts
    ]

    rows.controls = {
        "as_of": AS_OF.isoformat(),
        "ar_control": sum(row["open_amount"] for row in rows.invoices),
        "ap_control": sum(row["open_amount"] for row in rows.bills),
        "cash_control": sum(row["amount"] for row in rows.movements),
        "currency": tenant.currency,
    }
    return rows


def _category(name: str) -> str:
    lowered = name.lower()
    for token, category in (
        ("actuator", "components"),
        ("bearing", "components"),
        ("control", "components"),
        ("steel", "raw_materials"),
        ("fuel", "raw_materials"),
        ("textile", "raw_materials"),
        ("freight", "logistics"),
        ("logistics", "logistics"),
        ("forwarding", "logistics"),
        ("leasing", "fleet"),
        ("tyre", "fleet"),
        ("facilit", "facilities"),
        ("terminal", "facilities"),
        ("staffing", "services"),
        ("insurance", "services"),
        ("cloud", "services"),
        ("software", "services"),
        ("systems", "services"),
        ("legal", "services"),
        ("media", "services"),
        ("security", "services"),
        ("pharmaceutic", "consumables"),
        ("supply", "consumables"),
        ("reagent", "consumables"),
        ("packaging", "consumables"),
        ("tooling", "consumables"),
    ):
        if token in lowered:
            return category
    return "other"


# --- writing a styled database -------------------------------------------------------------

ENTITY_ROWS = (
    ("Customer", "customers"),
    ("Vendor", "suppliers"),
    ("Invoice", "invoices"),
    ("VendorInvoice", "bills"),
    ("BankAccount", "accounts"),
    ("BankTransaction", "movements"),
)

MONEY_FIELDS = frozenset({"amount", "open_amount", "balance"})


def _create_statements(style: Style) -> list[str]:
    statements: list[str] = []
    for entity, _ in ENTITY_ROWS:
        columns = style.columns[entity]
        types = style.types[entity]
        rendered = [
            f'    "{columns[field_name]}" {types[field_name]}'
            + (" PRIMARY KEY" if field_name == "external_id" else "")
            for field_name in columns
            if field_name in types
        ]
        statements.append(
            f'CREATE TABLE "{style.tables[entity]}" (\n' + ",\n".join(rendered) + "\n)"
        )
    as_of, ar, ap, cash, currency = style.controls_columns
    statements.append(
        f'CREATE TABLE "{style.controls_table}" (\n'
        f'    "{as_of}" DATE PRIMARY KEY,\n'
        f'    "{ar}" DECIMAL(18,2) NOT NULL,\n'
        f'    "{ap}" DECIMAL(18,2) NOT NULL,\n'
        f'    "{cash}" DECIMAL(18,2) NOT NULL,\n'
        f'    "{currency}" VARCHAR(3) NOT NULL\n)'
    )
    return statements


def write(url: str, tenant: Tenant, rows: Rows) -> dict[str, Any]:
    style = tenant.style
    engine = create_engine(url)
    counts: dict[str, int] = {}
    try:
        with engine.begin() as connection:
            for table in [
                style.controls_table,
                *reversed([style.tables[e] for e, _ in ENTITY_ROWS]),
            ]:
                connection.exec_driver_sql(f'DROP TABLE IF EXISTS "{table}"')
            for statement in _create_statements(style):
                connection.exec_driver_sql(statement)

            for entity, attribute in ENTITY_ROWS:
                source_rows = getattr(rows, attribute)
                columns = style.columns[entity]
                types = style.types[entity]
                fields = [name for name in columns if name in types]
                placeholders = ", ".join(f":{name}" for name in fields)
                names = ", ".join(f'"{columns[name]}"' for name in fields)
                payload = [
                    {
                        name: (
                            render_money(row[name], style.units)
                            if name in MONEY_FIELDS
                            else row.get(name)
                        )
                        for name in fields
                    }
                    for row in source_rows
                ]
                if payload:
                    connection.execute(
                        text(
                            f'INSERT INTO "{style.tables[entity]}" ({names}) VALUES ({placeholders})'
                        ),
                        payload,
                    )
                counts[style.tables[entity]] = len(source_rows)

            as_of, ar, ap, cash, currency = style.controls_columns
            connection.execute(
                text(
                    f'INSERT INTO "{style.controls_table}" '
                    f'("{as_of}", "{ar}", "{ap}", "{cash}", "{currency}") '
                    "VALUES (:as_of, :ar, :ap, :cash, :currency)"
                ),
                [
                    {
                        "as_of": rows.controls["as_of"],
                        "ar": major(rows.controls["ar_control"]),
                        "ap": major(rows.controls["ap_control"]),
                        "cash": major(rows.controls["cash_control"]),
                        "currency": rows.controls["currency"],
                    }
                ],
            )
    finally:
        engine.dispose()

    return {
        "key": tenant.key,
        "company": tenant.company,
        "industry": tenant.industry,
        "headline": tenant.headline,
        "schema_style": style.label,
        "units": style.units,
        "currency": tenant.currency,
        "url": url,
        "as_of": AS_OF.isoformat(),
        "tables": counts,
        "control_balances": {
            "as_of": rows.controls["as_of"],
            "ar_control_minor": rows.controls["ar_control"],
            "ap_control_minor": rows.controls["ap_control"],
            "cash_control_minor": rows.controls["cash_control"],
            "currency": rows.controls["currency"],
        },
    }


def default_url(tenant: Tenant, directory: Path) -> str:
    return f"sqlite:///{directory / f'demo-source-{tenant.key}.db'}"


def build_all(directory: Path, *, keys: list[str] | None = None) -> list[dict[str, Any]]:
    directory.mkdir(parents=True, exist_ok=True)
    chosen = [BY_KEY[key] for key in keys] if keys else list(TENANTS)
    return [write(default_url(tenant, directory), tenant, build(tenant)) for tenant in chosen]


def main() -> None:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description="Build the demo tenants' source databases.")
    parser.add_argument(
        "--dir",
        default=os.environ.get("DEMO_SOURCE_DIR", "var/demo"),
        help="directory the SQLite source databases are written to",
    )
    parser.add_argument(
        "--only", nargs="*", choices=sorted(BY_KEY), help="build only these tenants"
    )
    parser.add_argument("--manifest", default=str(MANIFEST))
    arguments = parser.parse_args()

    summaries = build_all(Path(arguments.dir), keys=arguments.only)
    manifest = Path(arguments.manifest)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"as_of": AS_OF.isoformat(), "companies": summaries}, indent=2) + "\n",
        encoding="utf-8",
    )

    for summary in summaries:
        controls = summary["control_balances"]
        print(f"\n{summary['company']}  ({summary['schema_style']}, {summary['units']} units)")
        print(f"  {summary['url']}")
        for table, count in summary["tables"].items():
            print(f"    {table:<22} {count:>6}")
        print(
            f"    control AR/AP/cash     "
            f"{major(controls['ar_control_minor'])} / "
            f"{major(controls['ap_control_minor'])} / "
            f"{major(controls['cash_control_minor'])} {controls['currency']}"
        )
    print(f"\nmanifest: {manifest}")


if __name__ == "__main__":
    main()
