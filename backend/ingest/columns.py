"""The deterministic half of the Cartographer: table and column classification.

Real customer schemas are not the three shapes in `templates.py`. An ERP calls its
receivables `ar_open_items`, its customers `cust_master`, and its amounts `gross_amt`
— none of which a template library will ever hold. This module is what makes the
DB Agent work on a schema nobody has seen before, and it does it with a lexicon and
arithmetic rather than a model, for the same reason the covenant engine does: the
answer has to be reproducible and reviewable.

The output is a `CartographerProposal` — the same structure the agent runtime
returns — so the pipeline treats a deterministic proposal and a model proposal
identically, and can merge one into the other. What the model is for is the residue:
columns this lexicon scored below threshold. Nothing here or there is trusted on its
own; the reconciliation gate in `validate.py` is still what accepts a mapping.

Confidences are basis points, never floats, so a threshold is an exact comparison.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

from backend.ingest.agents import (
    CartographerProposal,
    ColumnMapping,
    TableClassification,
    UnitCurrencyFinding,
)
from backend.ingest.fingerprint import SchemaFingerprint, TableProfile

#: A mapping is only proposed above this. Below it the column joins the residue.
ACCEPT_BPS = 6_000

#: Entity role -> tokens that name that table in the wild. Order is not significant;
#: scoring is over token overlap, so `ar_open_items` and `open_receivables` both land.
_TABLE_LEXICON: dict[str, tuple[str, ...]] = {
    "Customer": ("customer", "customers", "cust", "client", "clients", "debtor", "buyer", "master"),
    "Invoice": (
        "invoice",
        "invoices",
        "receivable",
        "receivables",
        "ar",
        "sales",
        "billing",
        "open",
        "items",
        "doc",
    ),
    "Vendor": ("vendor", "vendors", "supplier", "suppliers", "creditor", "payee", "master"),
    "VendorInvoice": (
        "bill",
        "bills",
        "payable",
        "payables",
        "ap",
        "purchase",
        "vendor",
        "open",
        "items",
    ),
    "BankAccount": ("bank", "account", "accounts", "cash", "acct"),
    "BankTransaction": (
        "transaction",
        "transactions",
        "txn",
        "ledger",
        "statement",
        "movement",
        "entries",
        "bank",
    ),
}

#: Tokens that decide *which side* of the ledger an ambiguous table sits on. An
#: `open_items` table joined to customers is AR; joined to suppliers it is AP.
_SIDE_HINTS: dict[str, tuple[str, ...]] = {
    "Invoice": ("ar", "receivable", "customer", "cust", "client", "sales", "debtor"),
    "VendorInvoice": ("ap", "payable", "vendor", "supplier", "purchase", "creditor"),
    "Customer": ("customer", "cust", "client", "debtor"),
    "Vendor": ("vendor", "supplier", "creditor", "payee"),
}

#: Canonical field -> column-name tokens, most specific first. A column matches on
#: whole-token overlap, so `gross_amt` matches `amt` but `amortisation` does not.
_FIELD_LEXICON: dict[str, dict[str, tuple[str, ...]]] = {
    "Customer": {
        "external_id": ("cust_code", "customer_id", "customer_code", "cust_id", "code", "id"),
        "name": ("cust_name", "customer_name", "company_name", "display_name", "name"),
    },
    "Invoice": {
        "external_id": (
            "invoice_ref",
            "invoice_no",
            "invoice_number",
            "doc_no",
            "document_no",
            "invoice_id",
            "id",
        ),
        "customer_ref": ("cust_code", "customer_id", "customer_code", "cust_id", "customer"),
        "amount": (
            "gross_amt",
            "amount_due",
            "invoice_amount",
            "gross_amount",
            "amount",
            "amt",
            "value",
            "total",
        ),
        "open_amount": ("open_amt", "balance", "outstanding", "open_amount", "residual"),
        "currency": ("currency", "curr", "ccy", "currency_code"),
        "due_date": ("due_dt", "due_date", "date_due", "maturity"),
        "issued_date": (
            "doc_dt",
            "issue_date",
            "issued_date",
            "invoice_date",
            "posted_on",
            "posting_date",
            "created",
            "created_at",
        ),
        "status": ("status", "state", "doc_status"),
    },
    "Vendor": {
        "external_id": ("supp_code", "vendor_id", "supplier_id", "vendor_code", "code", "id"),
        "name": ("supp_name", "vendor_name", "supplier_name", "display_name", "name"),
        "criticality": ("criticality", "risk_tier", "tier", "class", "category"),
    },
    "VendorInvoice": {
        "external_id": ("invoice_ref", "bill_no", "doc_no", "document_no", "invoice_no", "id"),
        "vendor_ref": (
            "supp_code",
            "vendor_id",
            "supplier_id",
            "vendor_code",
            "vendor",
            "supplier",
        ),
        "amount": ("gross_amt", "amount_due", "gross_amount", "amount", "amt", "value", "total"),
        "open_amount": ("open_amt", "balance", "outstanding", "open_amount", "residual"),
        "currency": ("currency", "curr", "ccy", "currency_code"),
        "due_date": ("due_dt", "due_date", "date_due", "net_due"),
        "issued_date": (
            "doc_dt",
            "bill_date",
            "issue_date",
            "issued_date",
            "posted_on",
            "posting_date",
            "created",
            "created_at",
        ),
        "status": ("status", "state", "doc_status"),
    },
    "BankAccount": {
        "external_id": ("acct_no", "account_no", "account_id", "account_number", "iban", "id"),
        "name": ("bank_name", "account_name", "nickname", "name", "description"),
        "currency": ("currency", "curr", "ccy", "currency_code"),
        "balance": ("balance", "current_balance", "closing_balance", "ledger_balance"),
    },
    "BankTransaction": {
        "external_id": ("entry_id", "txn_id", "transaction_id", "statement_line", "id"),
        "account_ref": ("acct_no", "account_no", "account_id", "account_number", "iban"),
        "amount": ("amt", "amount", "value", "signed_amount"),
        "currency": ("currency", "curr", "ccy", "currency_code"),
        "booked_on": ("value_dt", "booking_date", "value_date", "posted_on", "txn_date", "date"),
        "description": ("narrative", "description", "memo", "details", "reference"),
    },
}

#: Field -> the column types it may plausibly be drawn from. A `due_date` mapped onto
#: a boolean is a mis-map the lexicon alone would happily make; this is the check.
_TYPE_EXPECTATION: dict[str, tuple[str, ...]] = {
    "amount": ("numeric", "decimal", "money", "int", "bigint", "double", "real", "float"),
    "open_amount": ("numeric", "decimal", "money", "int", "bigint", "double", "real", "float"),
    "balance": ("numeric", "decimal", "money", "int", "bigint", "double", "real", "float"),
    "due_date": ("date", "timestamp", "datetime"),
    "issued_date": ("date", "timestamp", "datetime"),
    "booked_on": ("date", "timestamp", "datetime"),
    "currency": ("char", "varchar", "text", "string"),
}

_UNIT_MINOR_TOKENS = ("minor", "cents", "pence", "_bps")


@dataclass(frozen=True, slots=True)
class Residue:
    """What the lexicon could not resolve. This, and only this, is what a model sees."""

    tables: tuple[str, ...]
    columns: tuple[tuple[str, str], ...]


#: Token synonyms, applied before anything is compared. This is the single reason the
#: lexicon generalises: `client_code`, `cust_code` and `customerCode` are three spellings
#: of one idea, and normalising them here means the field lexicon holds the idea once
#: instead of holding every spelling anyone has ever used.
#:
#: A token mapping to the empty string is dropped. `amount_cents` and `amount` are the
#: same field; the *unit* that `cents` implies is read separately, by `_units_for`.
_SYNONYMS: dict[str, str] = {
    # counterparties
    "cust": "customer",
    "customers": "customer",
    "client": "customer",
    "clients": "customer",
    "debtor": "customer",
    "debtors": "customer",
    "buyer": "customer",
    "supp": "supplier",
    "suppliers": "supplier",
    "vendor": "supplier",
    "vendors": "supplier",
    "creditor": "supplier",
    "creditors": "supplier",
    "payee": "supplier",
    # money
    "amt": "amount",
    "amounts": "amount",
    "gross": "amount",
    "value": "amount",
    "total": "amount",
    "bal": "balance",
    "balances": "balance",
    "cents": "",
    "cent": "",
    "minor": "",
    "pence": "",
    "pennies": "",
    # currency, dates, identity
    "ccy": "currency",
    "curr": "currency",
    "currencies": "currency",
    "dt": "date",
    "dates": "date",
    "day": "date",
    "id": "identifier",
    "ids": "identifier",
    "code": "identifier",
    "key": "identifier",
    "no": "number",
    "num": "number",
    "nbr": "number",
    "ref": "reference",
    "refs": "reference",
    # documents and movements
    "doc": "document",
    "docs": "document",
    "documents": "document",
    "inv": "invoice",
    "invoices": "invoice",
    "txn": "transaction",
    "trans": "transaction",
    "transactions": "transaction",
    "movement": "transaction",
    "movements": "transaction",
    "entries": "transaction",
    "acct": "account",
    "acc": "account",
    "accounts": "account",
    "desc": "description",
    "memo": "description",
    "narrative": "description",
    "details": "description",
    "detail": "description",
    "posted": "booked",
    "booking": "booked",
    "items": "item",
    "receivables": "receivable",
    "payables": "payable",
}


def _split(name: str) -> list[str]:
    """Break an identifier at separators and at camelCase boundaries.

    The case split has to happen *before* lowercasing, which is the whole subtlety:
    lowercase `customerMaster` first and the boundary it is named after is gone.
    """
    cleaned = "".join(character if character.isalnum() else " " for character in name)
    parts: list[str] = []
    for chunk in cleaned.split():
        current = chunk[:1]
        for previous, character in pairwise(chunk):
            if character.isupper() and not previous.isupper():
                parts.append(current)
                current = character
            else:
                current += character
        parts.append(current)
    return [part.lower() for part in parts if part]


def tokens(name: str) -> set[str]:
    """Canonical tokens for an identifier: `custCode` and `client_code` both give
    `{customer, identifier}`, which is what lets one lexicon entry cover both."""
    resolved = {_SYNONYMS.get(part, part) for part in _split(name)}
    return {part for part in resolved if part}


def _overlap_bps(candidate: set[str], lexicon: tuple[str, ...]) -> int:
    """Basis points of the lexicon's tokens present in the candidate identifier."""
    lexicon_tokens: set[str] = set()
    for entry in lexicon:
        lexicon_tokens |= tokens(entry)
    if not lexicon_tokens:
        return 0
    hits = len(candidate & lexicon_tokens)
    return (10_000 * hits) // max(len(candidate), 1)


def classify_table(table: TableProfile) -> tuple[str, int] | None:
    """Best entity role for one table, with a confidence in basis points."""
    name_tokens = tokens(table.name)
    column_tokens: set[str] = set()
    for column in table.columns:
        column_tokens |= tokens(column.name)

    best: tuple[str, int] | None = None
    for role, lexicon in _TABLE_LEXICON.items():
        score = _overlap_bps(name_tokens, lexicon)
        # A table's columns are better evidence of what it holds than its name. An
        # `open_items` table with `cust_code` is receivables; with `supp_code` it is not.
        hints = _SIDE_HINTS.get(role, ())
        if hints:
            hint_tokens: set[str] = set()
            for hint in hints:
                hint_tokens |= tokens(hint)
            if column_tokens & hint_tokens:
                score += 2_500
            elif name_tokens & hint_tokens:
                score += 1_000
        # Field coverage: how much of the entity this table could actually populate.
        fields = _FIELD_LEXICON.get(role, {})
        matched = sum(
            1
            for field, lex in fields.items()
            if any(
                _column_score(column.name, column.data_type, field, lex) >= ACCEPT_BPS
                for column in table.columns
            )
        )
        if fields:
            score += (2_500 * matched) // len(fields)
        score = min(score, 10_000)
        if best is None or score > best[1]:
            best = (role, score)
    if best is None or best[1] < ACCEPT_BPS:
        return None
    return best


def _column_score(column: str, data_type: str, field: str, lexicon: tuple[str, ...]) -> int:
    """Score one column against one canonical field.

    Comparison is between *canonical token sets*, not strings, which is what makes
    `riskTier` and `risk_tier` the same answer. An exact set match scores near the top,
    with a small rank penalty so a lexicon's earlier, more specific entries win ties —
    `risk_tier` beats `category` for criticality because it was listed as the better
    name for it, not because of anything about the column.

    A partial match is scored by Dice coefficient, which is the right shape here: it
    rewards a column that covers the entry without being padded with tokens the entry
    does not have, so `customer_id` does not quietly become an invoice's own reference.
    """
    column_tokens = tokens(column)
    if not column_tokens:
        return 0

    best = 0
    for rank, entry in enumerate(lexicon):
        entry_tokens = tokens(entry)
        if not entry_tokens:
            continue
        if column_tokens == entry_tokens:
            best = max(best, 10_000 - rank * 100)
            continue
        shared = len(column_tokens & entry_tokens)
        if not shared:
            continue
        dice = (2 * 10_000 * shared) // (len(column_tokens) + len(entry_tokens))
        best = max(best, max(0, (dice * 8_500) // 10_000 - rank * 50))

    expected = _TYPE_EXPECTATION.get(field)
    if expected is not None:
        if any(token in data_type for token in expected):
            best = min(10_000, best + 1_500)
        else:
            # Wrong type is disqualifying, not merely discouraging: a date field read
            # from a boolean column produces rows that fail loudly much later.
            return 0
    return best


def map_columns(table: TableProfile, role: str) -> list[ColumnMapping]:
    """Best column per canonical field for one classified table. One column, one field."""
    fields = _FIELD_LEXICON.get(role, {})
    claimed: dict[str, tuple[str, int]] = {}  # column -> (field, score)
    chosen: dict[str, tuple[str, int]] = {}  # field -> (column, score)

    for field, lexicon in fields.items():
        for column in table.columns:
            score = _column_score(column.name, column.data_type, field, lexicon)
            if score < ACCEPT_BPS:
                continue
            held = chosen.get(field)
            if held is not None and held[1] >= score:
                continue
            # A column already claimed by a better-scoring field stays where it is.
            owner = claimed.get(column.name)
            if owner is not None and owner[1] >= score:
                continue
            if owner is not None:
                chosen.pop(owner[0], None)
            chosen[field] = (column.name, score)
            claimed[column.name] = (field, score)

    return [
        ColumnMapping(table=table.name, column=column, canonical_field=field, confidence_bps=score)
        for field, (column, score) in sorted(chosen.items())
    ]


def _units_for(table: TableProfile, mappings: list[ColumnMapping]) -> list[UnitCurrencyFinding]:
    """Minor or major units, and where the currency comes from.

    Guessing wrong here is a hundredfold error, so the rule is conservative: a column
    is minor-unit only when it says so in its name or its type is integral. Everything
    else is treated as major units and parsed by digit-splitting, never by float.
    """
    has_currency_column = any(m.canonical_field == "currency" for m in mappings)
    by_name = {column.name: column for column in table.columns}
    findings: list[UnitCurrencyFinding] = []
    for mapping in mappings:
        if mapping.canonical_field not in {"amount", "open_amount", "balance"}:
            continue
        column = by_name[mapping.column]
        name = column.name.lower()
        integral = any(token in column.data_type for token in ("int", "bigint", "smallint"))
        if any(token in name for token in _UNIT_MINOR_TOKENS):
            units, confidence = "minor", 9_500
        elif integral:
            units, confidence = "minor", 7_000
        else:
            units, confidence = "major", 9_000
        findings.append(
            UnitCurrencyFinding(
                table=table.name,
                column=column.name,
                units=units,
                currency_source="column" if has_currency_column else "tenant_default",
                confidence_bps=confidence,
            )
        )
    return findings


def propose(fingerprint: SchemaFingerprint) -> tuple[CartographerProposal, Residue]:
    """Classify every table and map every column the lexicon is confident about.

    Returns the proposal and the residue. The residue is the honest part: tables the
    lexicon could not name and mapped tables missing a field the entity needs.
    """
    classifications: list[TableClassification] = []
    columns: list[ColumnMapping] = []
    units: list[UnitCurrencyFinding] = []
    residual_tables: list[str] = []
    residual_columns: list[tuple[str, str]] = []

    # One table per entity role: the highest-scoring candidate wins, and the others
    # become residue. Two tables claiming to be Invoice is a question for a human.
    winners: dict[str, tuple[TableProfile, int]] = {}
    for table in fingerprint.tables:
        verdict = classify_table(table)
        if verdict is None:
            residual_tables.append(table.name)
            continue
        role, score = verdict
        held = winners.get(role)
        if held is None or score > held[1]:
            if held is not None:
                residual_tables.append(held[0].name)
            winners[role] = (table, score)
        else:
            residual_tables.append(table.name)

    for role, (table, score) in sorted(winners.items()):
        classifications.append(
            TableClassification(table=table.name, entity_role=role, confidence_bps=score)
        )
        mapped = map_columns(table, role)
        columns.extend(mapped)
        units.extend(_units_for(table, mapped))
        found = {mapping.canonical_field for mapping in mapped}
        residual_columns.extend(
            (table.name, field) for field in sorted(_FIELD_LEXICON[role]) if field not in found
        )

    proposal = CartographerProposal(
        tables=tuple(classifications),
        columns=tuple(columns),
        units=tuple(units),
    )
    return proposal, Residue(tables=tuple(sorted(residual_tables)), columns=tuple(residual_columns))


def merge(base: CartographerProposal, extra: CartographerProposal) -> CartographerProposal:
    """Fold a model's proposal into the deterministic one. The lexicon wins ties.

    A model may only fill gaps. It cannot overwrite a mapping the lexicon made, because
    a reviewable rule losing to an unreviewable one is the wrong default in a system
    whose mappings decide what a cash number means.
    """
    held_tables = {table.table for table in base.tables}
    held_columns = {(column.table, column.canonical_field) for column in base.columns}
    held_units = {(unit.table, unit.column) for unit in base.units}
    return CartographerProposal(
        tables=base.tables + tuple(t for t in extra.tables if t.table not in held_tables),
        columns=base.columns
        + tuple(c for c in extra.columns if (c.table, c.canonical_field) not in held_columns),
        account_roles=base.account_roles + extra.account_roles,
        units=base.units + tuple(u for u in extra.units if (u.table, u.column) not in held_units),
    )


__all__ = [
    "ACCEPT_BPS",
    "Residue",
    "classify_table",
    "map_columns",
    "merge",
    "propose",
    "tokens",
]
