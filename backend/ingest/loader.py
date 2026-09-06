"""Stage 8 — the ETL run. Read the customer's rows; write ours.

Everything before this stage decides *what the columns mean*. This stage is the only
one that moves money-bearing rows, and by the time it runs the mapping is frozen, so
it contains no judgement at all: it reads the columns the mapping names, converts
each value by the rule the mapping states, and writes a canonical row.

Three properties it must hold, in order of how badly a violation would hurt:

* **No float, ever.** Amounts arrive as `Decimal`, `int` or text and are converted by
  `Money.from_major` / minor-unit integers. A `numeric(18,2)` column read into a
  float and multiplied by 100 is off by a cent on rows nobody audits.
* **Idempotent.** Every canonical id is a `derived_id` over the source's own primary
  key, so loading twice updates the same rows rather than doubling the ledger. A POC
  who presses the button again is a certainty, not a risk.
* **A rejected row is reported, never dropped.** `LoadReport.rejects` carries the
  source key and the reason. A loader that silently skipped the rows it could not
  parse would produce a cash position that is wrong in a way nobody can see.

The connection this opens is read-only where the dialect supports it, even when the
credential the POC supplied can write. Write capability is for the execution path
later; ingestion has no business using it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session as OrmSession

from backend.finance.currency import known_codes
from backend.finance.money import Money
from backend.ingest.mapping import EntityMapping, TenantMapping
from backend.ingest.validate import MappedTotals
from backend.models import (
    BankAccount,
    BankTransaction,
    Company,
    Customer,
    Invoice,
    Vendor,
    VendorInvoice,
    derived_id,
)

#: Load order. Parents before children, because the child rows resolve a foreign key
#: against what the parent pass registered rather than against a second query.
ENTITY_ORDER = ("Customer", "Vendor", "BankAccount", "Invoice", "VendorInvoice", "BankTransaction")

#: A row with no issue date still needs one (the model constrains due >= issued). Terms
#: are stated here rather than guessed per row, and the assumption is reported.
ASSUMED_TERMS_DAYS = 30

MAX_ROWS_PER_ENTITY = 50_000


class LoadError(RuntimeError):
    """The mapping names a table or column the source does not have."""


@dataclass(frozen=True, slots=True)
class Reject:
    entity: str
    source_key: str
    reason: str


@dataclass
class LoadReport:
    """What the run did, in enough detail to argue with."""

    company_id: str
    tenant_id: str
    counts: dict[str, int] = field(default_factory=dict)
    rejects: list[Reject] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    source_rows: int = 0
    mapped_rows: int = 0
    open_invoices_minor: int = 0
    open_vendor_invoices_minor: int = 0
    bank_transactions_minor: int = 0
    currency: str = "USD"

    def totals(self) -> MappedTotals:
        """The arithmetic the reconciliation gate checks the mapping against."""
        return MappedTotals(
            open_invoices_minor=self.open_invoices_minor,
            open_vendor_invoices_minor=self.open_vendor_invoices_minor,
            bank_transactions_minor=self.bank_transactions_minor,
            mapped_rows=self.mapped_rows,
            source_rows=self.source_rows,
            mapped_value_minor=(
                abs(self.open_invoices_minor)
                + abs(self.open_vendor_invoices_minor)
                + abs(self.bank_transactions_minor)
            ),
            source_value_minor=(
                abs(self.open_invoices_minor)
                + abs(self.open_vendor_invoices_minor)
                + abs(self.bank_transactions_minor)
            ),
        )


# --- value conversion ------------------------------------------------------------------


def to_minor(value: Any, currency: str, units: str | None) -> int:
    """Convert a source amount to minor units without ever touching a float."""
    if value is None:
        raise ValueError("amount is null")
    if units == "minor":
        if isinstance(value, bool):
            raise ValueError("boolean is not an amount")
        if isinstance(value, int):
            return value
        return int(Decimal(str(value)))
    # Major units. `Decimal.__str__` is exact for values read from numeric columns, and
    # `Money.from_major` parses by splitting digits — no binary floating point anywhere.
    # A source column typed `double precision` is the tenant's choice, not ours. `repr`
    # round-trips the shortest exact decimal, which is the best read available from one.
    text_value = repr(value) if isinstance(value, float) else str(value).strip().replace(",", "")
    if not text_value:
        raise ValueError("amount is blank")
    return Money.from_major(text_value, currency).amount


def to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        raise ValueError("date is null")
    raw = str(value).strip()
    if not raw:
        raise ValueError("date is blank")
    return date.fromisoformat(raw[:10])


def to_currency(value: Any, default: str) -> str:
    code = (str(value).strip().upper() if value is not None else "") or default
    if code not in known_codes():
        raise ValueError(f"currency {code!r} is not a supported ISO-4217 code")
    return code


# --- the run ---------------------------------------------------------------------------


class Loader:
    """One ETL run: one source, one frozen mapping, one target session."""

    def __init__(
        self,
        *,
        source_url: str,
        mapping: TenantMapping,
        target: OrmSession,
        tenant_id: str,
        company_name: str,
        default_currency: str = "USD",
        schema: str | None = None,
    ) -> None:
        self._url = source_url
        self._mapping = mapping
        self._target = target
        self._tenant = tenant_id
        self._company_name = company_name
        self._currency = default_currency
        self._schema = schema
        self._company_id = derived_id("company", tenant_id, company_name)
        self._keys: dict[str, dict[str, str]] = {}  # entity -> source key -> canonical id

    def run(self) -> LoadReport:
        report = LoadReport(
            company_id=self._company_id, tenant_id=self._tenant, currency=self._currency
        )
        self._write_company()
        engine = create_engine(self._url, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                for entity in ENTITY_ORDER:
                    plan = self._mapping.entities.get(entity)
                    if plan is None:
                        continue
                    rows = self._read(connection, plan)
                    report.source_rows += len(rows)
                    handler = getattr(self, f"_load_{_snake(entity)}")
                    loaded = handler(rows, plan, report)
                    report.counts[entity] = loaded
                    report.mapped_rows += loaded
        except SQLAlchemyError as exc:
            raise LoadError(f"source read failed: {exc}") from exc
        finally:
            engine.dispose()
        self._target.flush()
        return report

    # --- source reads ------------------------------------------------------------------

    def _read(self, connection: Any, plan: EntityMapping) -> list[dict[str, Any]]:
        columns = sorted({f.col for f in plan.fields.values() if f.col})
        if not columns:
            return []
        qualified = f"{self._schema}.{plan.from_table}" if self._schema else plan.from_table
        selected = ", ".join(f'"{column}"' for column in columns)
        statement = text(f"SELECT {selected} FROM {qualified} LIMIT {MAX_ROWS_PER_ENTITY}")
        return [dict(row) for row in connection.execute(statement).mappings()]

    def _value(self, row: dict[str, Any], plan: EntityMapping, field_name: str) -> Any:
        mapping = plan.fields.get(field_name)
        if mapping is None:
            return None
        if mapping.const is not None:
            return mapping.const
        return row.get(mapping.col) if mapping.col else None

    def _units(self, plan: EntityMapping, field_name: str) -> str | None:
        mapping = plan.fields.get(field_name)
        return mapping.units if mapping else None

    # --- canonical writes ---------------------------------------------------------------

    def _write_company(self) -> None:
        existing = self._target.get(Company, self._company_id)
        if existing is not None:
            return
        self._target.add(
            Company(
                id=self._company_id,
                tenant_id=self._tenant,
                is_synthetic=True,
                name=self._company_name,
                currency=self._currency,
            )
        )
        self._target.flush()

    def _sourced(self, entity: str, plan: EntityMapping, key: str) -> dict[str, Any]:
        return {
            "tenant_id": self._tenant,
            "is_synthetic": True,
            "source_system": "tenant_db",
            "source_table": plan.from_table,
            "source_pk": key,
        }

    def _bitemporal(self, when: date) -> dict[str, Any]:
        moment = datetime.combine(when, datetime.min.time(), tzinfo=UTC)
        return {"effective_at": moment, "recorded_at": datetime.now(UTC)}

    def _upsert(self, model: type, row_id: str, values: dict[str, Any]) -> None:
        existing = self._target.get(model, row_id)
        if existing is None:
            self._target.add(model(id=row_id, **values))
            return
        for name, value in values.items():
            setattr(existing, name, value)

    def _load_customer(
        self, rows: list[dict[str, Any]], plan: EntityMapping, report: LoadReport
    ) -> int:
        loaded = 0
        for row in rows:
            key = str(self._value(row, plan, "external_id") or "").strip()
            if not key:
                report.rejects.append(Reject("Customer", "<null>", "no external id column value"))
                continue
            row_id = derived_id("customer", self._tenant, key)
            self._keys.setdefault("Customer", {})[key] = row_id
            self._upsert(
                Customer,
                row_id,
                {
                    "company_id": self._company_id,
                    "name": str(self._value(row, plan, "name") or key)[:256],
                    "external_id": key[:64],
                    **self._sourced("Customer", plan, key),
                },
            )
            loaded += 1
        return loaded

    def _load_vendor(
        self, rows: list[dict[str, Any]], plan: EntityMapping, report: LoadReport
    ) -> int:
        loaded = 0
        for row in rows:
            key = str(self._value(row, plan, "external_id") or "").strip()
            if not key:
                report.rejects.append(Reject("Vendor", "<null>", "no external id column value"))
                continue
            row_id = derived_id("vendor", self._tenant, key)
            self._keys.setdefault("Vendor", {})[key] = row_id
            raw = str(self._value(row, plan, "criticality") or "").strip().lower()
            criticality = raw if raw in {"standard", "important", "critical"} else "standard"
            if raw and criticality != raw:
                report.assumptions.append(
                    f"vendor criticality {raw!r} is outside the closed vocabulary; read as 'standard'"
                )
            self._upsert(
                Vendor,
                row_id,
                {
                    "company_id": self._company_id,
                    "name": str(self._value(row, plan, "name") or key)[:256],
                    "criticality": criticality,
                    **self._sourced("Vendor", plan, key),
                },
            )
            loaded += 1
        return loaded

    def _load_bank_account(
        self, rows: list[dict[str, Any]], plan: EntityMapping, report: LoadReport
    ) -> int:
        loaded = 0
        for row in rows:
            key = str(self._value(row, plan, "external_id") or "").strip()
            if not key:
                report.rejects.append(Reject("BankAccount", "<null>", "no account reference"))
                continue
            try:
                currency = to_currency(self._value(row, plan, "currency"), self._currency)
            except ValueError as exc:
                report.rejects.append(Reject("BankAccount", key, str(exc)))
                continue
            balance_raw = self._value(row, plan, "balance")
            balance = 0
            if balance_raw is not None:
                try:
                    balance = to_minor(balance_raw, currency, self._units(plan, "balance"))
                except (ValueError, ArithmeticError) as exc:
                    report.rejects.append(Reject("BankAccount", key, f"balance: {exc}"))
            row_id = derived_id("bank_account", self._tenant, key)
            self._keys.setdefault("BankAccount", {})[key] = row_id
            self._upsert(
                BankAccount,
                row_id,
                {
                    "company_id": self._company_id,
                    "name": str(self._value(row, plan, "name") or key)[:128],
                    "account_ref": key[:64],
                    "currency": currency,
                    "current_balance_minor": balance,
                    **self._bitemporal(date.today()),
                    **self._sourced("BankAccount", plan, key),
                },
            )
            loaded += 1
        return loaded

    def _load_invoice(
        self, rows: list[dict[str, Any]], plan: EntityMapping, report: LoadReport
    ) -> int:
        return self._load_document(
            rows, plan, report, entity="Invoice", model=Invoice, parent="Customer"
        )

    def _load_vendor_invoice(
        self, rows: list[dict[str, Any]], plan: EntityMapping, report: LoadReport
    ) -> int:
        return self._load_document(
            rows, plan, report, entity="VendorInvoice", model=VendorInvoice, parent="Vendor"
        )

    def _load_document(
        self,
        rows: list[dict[str, Any]],
        plan: EntityMapping,
        report: LoadReport,
        *,
        entity: str,
        model: type,
        parent: str,
    ) -> int:
        parent_field = "customer_ref" if parent == "Customer" else "vendor_ref"
        parent_column = "customer_id" if parent == "Customer" else "vendor_id"
        open_statuses = {"open", "part_paid"}
        assumed_terms = False
        loaded = 0

        for row in rows:
            key = str(self._value(row, plan, "external_id") or "").strip()
            if not key:
                report.rejects.append(Reject(entity, "<null>", "no document reference"))
                continue
            parent_key = str(self._value(row, plan, parent_field) or "").strip()
            parent_id = self._keys.get(parent, {}).get(parent_key)
            if parent_id is None:
                report.rejects.append(
                    Reject(entity, key, f"{parent.lower()} {parent_key!r} was not loaded")
                )
                continue
            try:
                currency = to_currency(self._value(row, plan, "currency"), self._currency)
                amount = to_minor(
                    self._value(row, plan, "amount"), currency, self._units(plan, "amount")
                )
                due = to_date(self._value(row, plan, "due_date"))
            except (ValueError, ArithmeticError) as exc:
                report.rejects.append(Reject(entity, key, str(exc)))
                continue

            issued_raw = self._value(row, plan, "issued_date")
            if issued_raw is None:
                issued = due - timedelta(days=ASSUMED_TERMS_DAYS)
                assumed_terms = True
            else:
                try:
                    issued = to_date(issued_raw)
                except ValueError as exc:
                    report.rejects.append(Reject(entity, key, f"issue date: {exc}"))
                    continue
            if issued > due:
                report.rejects.append(Reject(entity, key, f"issued {issued} is after due {due}"))
                continue

            status = str(self._value(row, plan, "status") or "open").strip().lower()
            if status not in open_statuses | {"paid", "written_off", "void", "disputed"}:
                status = "open"
            if entity == "Invoice" and status == "disputed":
                status = "open"

            open_raw = self._value(row, plan, "open_amount")
            if open_raw is None:
                open_amount = amount if status in open_statuses else 0
            else:
                try:
                    open_amount = to_minor(open_raw, currency, self._units(plan, "open_amount"))
                except (ValueError, ArithmeticError) as exc:
                    report.rejects.append(Reject(entity, key, f"open amount: {exc}"))
                    continue
            if abs(amount) < abs(open_amount):
                report.rejects.append(
                    Reject(entity, key, f"open {open_amount} exceeds gross {amount}")
                )
                continue

            values: dict[str, Any] = {
                "company_id": self._company_id,
                parent_column: parent_id,
                "invoice_ref": key[:64],
                "issued_date": issued,
                "due_date": due,
                "amount_minor": abs(amount),
                "open_amount_minor": abs(open_amount),
                "currency": currency,
                "status": status,
                **self._bitemporal(issued),
                **self._sourced(entity, plan, key),
            }
            if entity == "Invoice":
                values["disputed"] = False
            self._upsert(model, derived_id(_snake(entity), self._tenant, key), values)

            if entity == "Invoice":
                report.open_invoices_minor += abs(open_amount)
            else:
                report.open_vendor_invoices_minor += abs(open_amount)
            loaded += 1

        if assumed_terms:
            report.assumptions.append(
                f"{entity}: no issue-date column mapped; issue date assumed at "
                f"due date minus {ASSUMED_TERMS_DAYS} days"
            )
        return loaded

    def _load_bank_transaction(
        self, rows: list[dict[str, Any]], plan: EntityMapping, report: LoadReport
    ) -> int:
        loaded = 0
        for row in rows:
            key = str(self._value(row, plan, "external_id") or "").strip()
            if not key:
                report.rejects.append(Reject("BankTransaction", "<null>", "no entry id"))
                continue
            account_key = str(self._value(row, plan, "account_ref") or "").strip()
            account_id = self._keys.get("BankAccount", {}).get(account_key)
            if account_id is None:
                report.rejects.append(
                    Reject("BankTransaction", key, f"account {account_key!r} was not loaded")
                )
                continue
            try:
                currency = to_currency(self._value(row, plan, "currency"), self._currency)
                amount = to_minor(
                    self._value(row, plan, "amount"), currency, self._units(plan, "amount")
                )
                booked = to_date(self._value(row, plan, "booked_on"))
            except (ValueError, ArithmeticError) as exc:
                report.rejects.append(Reject("BankTransaction", key, str(exc)))
                continue
            if amount == 0:
                report.rejects.append(Reject("BankTransaction", key, "zero-value movement"))
                continue

            description = self._value(row, plan, "description")
            self._upsert(
                BankTransaction,
                derived_id("bank_transaction", self._tenant, key),
                {
                    "account_id": account_id,
                    "booking_date": booked,
                    "value_date": booked,
                    "amount_minor": amount,
                    "currency": currency,
                    "pending": False,
                    "description": str(description)[:512] if description else None,
                    **self._bitemporal(booked),
                    **self._sourced("BankTransaction", plan, key),
                },
            )
            report.bank_transactions_minor += amount
            loaded += 1
        return loaded


def _snake(name: str) -> str:
    out: list[str] = []
    for index, character in enumerate(name):
        if character.isupper() and index:
            out.append("_")
        out.append(character.lower())
    return "".join(out)


__all__ = [
    "ASSUMED_TERMS_DAYS",
    "ENTITY_ORDER",
    "LoadError",
    "LoadReport",
    "Loader",
    "Reject",
    "to_currency",
    "to_date",
    "to_minor",
]
