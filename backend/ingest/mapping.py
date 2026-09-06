"""Declarative tenant mapping freeze and capability manifest."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from backend.ingest.agents import CartographerProposal


class FieldMapping(BaseModel):
    model_config = ConfigDict(frozen=True)

    col: str | None = None
    const: str | None = None
    units: str | None = None
    fk: str | None = None
    reason: str | None = None


class EntityMapping(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_table: str
    fields: dict[str, FieldMapping]
    filters: tuple[dict[str, Any], ...] = ()


class CapabilityManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    bank_feed: bool = False
    gl: str = "absent"  # true | partial | absent as string for yaml friendliness
    ar_subledger: bool = False
    ap_subledger: bool = False
    fx: bool = False
    debt: bool = False
    dodo: bool = False
    payroll_calendar: bool = False


class TenantMapping(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int
    source: str
    confirmed_by: str
    confirmed_at: datetime
    coverage_rows_bps: int = Field(ge=0, le=10_000)
    coverage_value_bps: int = Field(ge=0, le=10_000)
    entities: dict[str, EntityMapping]
    gl_roles: dict[str, str] = Field(default_factory=dict)
    capabilities: CapabilityManifest = Field(default_factory=CapabilityManifest)
    fingerprint_hash: str = ""


def freeze_mapping(
    mapping: TenantMapping,
    *,
    directory: Path,
) -> Path:
    """Write `mapping.vN.yaml`. After freeze, runtime ETL is pure deterministic code."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"mapping.v{mapping.version}.yaml"
    payload = mapping.model_dump(mode="json")
    path.write_text(
        yaml.safe_dump(payload, sort_keys=True, default_flow_style=False),
        encoding="utf-8",
    )
    return path


def load_mapping(path: Path) -> TenantMapping:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return TenantMapping.model_validate(data)


def draft_from_template(
    *,
    template_id: str,
    source: str,
    fingerprint_hash: str,
    confirmed_by: str = "system",
) -> TenantMapping:
    """Produce a version-1 draft from a matched template id."""
    entities: dict[str, EntityMapping] = {}
    capabilities = CapabilityManifest()
    if template_id == "csv_upload":
        entities = {
            "Invoice": EntityMapping(
                from_table="invoices",
                fields={
                    "external_id": FieldMapping(col="invoice_ref"),
                    "customer_ref": FieldMapping(col="customer"),
                    "amount": FieldMapping(col="amount_minor", units="minor"),
                    "currency": FieldMapping(col="currency"),
                    "due_date": FieldMapping(col="due_date"),
                },
            ),
            "BankTransaction": EntityMapping(
                from_table="bank_transactions",
                fields={
                    "external_id": FieldMapping(col="txn_id"),
                    "amount": FieldMapping(col="amount_minor", units="minor"),
                    "currency": FieldMapping(col="currency"),
                    "booked_on": FieldMapping(col="booked_on"),
                },
            ),
            "VendorInvoice": EntityMapping(
                from_table="vendor_invoices",
                fields={
                    "external_id": FieldMapping(col="invoice_ref"),
                    "vendor_ref": FieldMapping(col="vendor"),
                    "amount": FieldMapping(col="amount_minor", units="minor"),
                    "currency": FieldMapping(col="currency"),
                    "due_date": FieldMapping(col="due_date"),
                },
            ),
        }
        capabilities = CapabilityManifest(
            bank_feed=True, ar_subledger=True, ap_subledger=True, gl="absent"
        )
    elif template_id == "dodo_saas":
        entities = {
            "Payment": EntityMapping(
                from_table="payments",
                fields={
                    "external_id": FieldMapping(col="payment_id"),
                    "amount": FieldMapping(col="amount", units="minor"),
                    "currency": FieldMapping(col="currency"),
                    "status": FieldMapping(col="status"),
                },
            ),
            "Subscription": EntityMapping(
                from_table="subscriptions",
                fields={
                    "external_id": FieldMapping(col="subscription_id"),
                    "customer_ref": FieldMapping(col="customer_id"),
                    "status": FieldMapping(col="status"),
                },
            ),
        }
        capabilities = CapabilityManifest(dodo=True, ar_subledger=True, gl="absent")
    elif template_id == "stripe_quickbooks":
        entities = {
            "Invoice": EntityMapping(
                from_table="invoices",
                fields={
                    "external_id": FieldMapping(col="id"),
                    "customer_ref": FieldMapping(col="customer_id", fk="customers.id"),
                    "amount": FieldMapping(col="amount_due", units="major"),
                    "currency": FieldMapping(col="currency"),
                    "due_date": FieldMapping(col="due_date"),
                },
            ),
            "Customer": EntityMapping(
                from_table="customers",
                fields={
                    "external_id": FieldMapping(col="id"),
                    "name": FieldMapping(col="name"),
                },
            ),
        }
        capabilities = CapabilityManifest(
            bank_feed=True, ar_subledger=True, ap_subledger=True, gl="partial"
        )
    else:
        raise ValueError(f"unknown template {template_id!r}")

    return TenantMapping(
        version=1,
        source=source,
        confirmed_by=confirmed_by,
        confirmed_at=datetime.now(UTC),
        coverage_rows_bps=10_000,
        coverage_value_bps=10_000,
        entities=entities,
        capabilities=capabilities,
        fingerprint_hash=fingerprint_hash,
    )


def draft_from_proposal(
    proposal: CartographerProposal,
    *,
    source: str,
    fingerprint_hash: str,
    confirmed_by: str = "system",
    default_currency: str = "USD",
) -> TenantMapping:
    """Turn a Cartographer proposal into a version-1 draft mapping.

    This is the path a schema nobody has a template for takes. It is deliberately
    mechanical: the proposal already carries the judgement, and a mapping that
    quietly improved on it would put a second opinion somewhere nobody reviews.

    A missing `currency` column becomes a `const` field rather than a hole, because
    a row without a currency is not loadable at all — and stating the tenant default
    explicitly in the frozen mapping is what makes that assumption reviewable.
    """
    from backend.ingest.columns import ACCEPT_BPS

    units_by_column = {(unit.table, unit.column): unit.units for unit in proposal.units}
    by_table = {table.table: table.entity_role for table in proposal.tables}

    grouped: dict[str, dict[str, FieldMapping]] = {}
    for column in proposal.columns:
        role = by_table.get(column.table)
        if role is None or column.confidence_bps < ACCEPT_BPS:
            continue
        grouped.setdefault(role, {})[column.canonical_field] = FieldMapping(
            col=column.column,
            units=units_by_column.get((column.table, column.column)),
            reason=f"cartographer {column.confidence_bps}bps",
        )

    table_for_role = {role: table for table, role in by_table.items()}
    entities: dict[str, EntityMapping] = {}
    for role, fields in grouped.items():
        if "currency" not in fields:
            fields["currency"] = FieldMapping(
                const=default_currency, reason="no currency column found; tenant default"
            )
        entities[role] = EntityMapping(from_table=table_for_role[role], fields=fields)

    capabilities = CapabilityManifest(
        bank_feed="BankTransaction" in entities,
        ar_subledger="Invoice" in entities,
        ap_subledger="VendorInvoice" in entities,
        gl="absent",
    )
    return TenantMapping(
        version=1,
        source=source,
        confirmed_by=confirmed_by,
        confirmed_at=datetime.now(UTC),
        coverage_rows_bps=0,
        coverage_value_bps=0,
        entities=entities,
        capabilities=capabilities,
        fingerprint_hash=fingerprint_hash,
    )


__all__ = [
    "CapabilityManifest",
    "EntityMapping",
    "FieldMapping",
    "TenantMapping",
    "draft_from_proposal",
    "draft_from_template",
    "freeze_mapping",
    "load_mapping",
]
