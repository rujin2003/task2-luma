"""Declarative tenant mapping freeze and capability manifest."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


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


__all__ = [
    "CapabilityManifest",
    "EntityMapping",
    "FieldMapping",
    "TenantMapping",
    "draft_from_template",
    "freeze_mapping",
    "load_mapping",
]
