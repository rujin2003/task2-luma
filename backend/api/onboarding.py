"""The DB Agent over HTTP: paste a URL, watch it read the schema, load the ledger.

Every route here is one stage of `backend/ingest/onboarding.py`, and the stage gates
live in that module rather than in these handlers — a UI that calls them out of order
gets a 409 with the name of the missing step, not a half-built mapping.

Two things this module is careful about, because both are easy to get wrong in a
screen that exists to accept a credential:

* **The URL is never echoed.** Requests carry it; responses carry `redact()`'d text.
  It is held in the onboarding object in memory and dropped on reset.
* **The load and the reconciliation share one transaction.** The rows are written,
  the tenant's own control totals are checked against what was written, and only a
  pass commits. A mapping that does not tie out leaves the target database exactly
  as it found it.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, select

from backend.api import store
from backend.api.session import AS_OF, DataSource
from backend.api.session import bind as bind_session
from backend.api.session import clear as clear_session
from backend.ingest.database import ConnectionError_
from backend.ingest.loader import LoadError
from backend.ingest.onboarding import STAGES, Onboarding, StageError
from backend.ingest.validate import BalanceSnapshot
from backend.models import (
    BankAccount,
    BankTransaction,
    Customer,
    Invoice,
    Vendor,
    VendorInvoice,
)
from backend.tools.tenant import TenantToolset
from backend.tools.toolset import ToolError

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

DEMO_MANIFEST = Path("var/demo-companies.json")

# One onboarding at a time. The session model is single-tenant by design (see
# `session.py`), and pretending otherwise here would be inventing a tenancy story the
# rest of the process cannot honour.
_current: Onboarding | None = None


def _require() -> Onboarding:
    if _current is None:
        raise HTTPException(status_code=409, detail="no source database is connected")
    return _current


def _stage_error(exc: StageError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


# --- request bodies ------------------------------------------------------------------


class ConnectRequest(BaseModel):
    """What the POC types. `url` carries the password; nothing sends it back."""

    url: Annotated[str, Field(min_length=1, max_length=2000)]
    company: Annotated[str, Field(min_length=1, max_length=256)]
    tenant_id: Annotated[str, Field(min_length=1, max_length=64)] = "tenant"
    currency: Annotated[str, Field(min_length=3, max_length=3)] = "USD"
    schema_name: str | None = None
    operator: Annotated[str, Field(max_length=128)] = "poc@tenant"


class ControlTotals(BaseModel):
    """The tenant's own trial-balance extract. This is what the mapping is held to.

    They are entered rather than derived on purpose: a mapping validated against
    numbers the mapping itself produced would validate anything.
    """

    ar_minor: int
    ap_minor: int
    cash_minor: int
    currency: Annotated[str, Field(min_length=3, max_length=3)] = "USD"
    cash_tolerance_minor: Annotated[int, Field(ge=0)] = 0


# --- responses -----------------------------------------------------------------------


class OnboardingStatus(BaseModel):
    connected: bool
    stages: list[str] = list(STAGES)
    state: dict[str, str] = Field(default_factory=dict)
    summary: dict[str, Any] | None = None
    fingerprint_hash: str | None = None
    table_count: int = 0
    entities: list[str] = Field(default_factory=list)
    mapping_path: str | None = None
    reconciled: bool | None = None
    reconciliation_failures: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


def _status() -> OnboardingStatus:
    if _current is None:
        return OnboardingStatus(connected=False, state=dict.fromkeys(STAGES, "pending"))
    return OnboardingStatus(
        connected=_current.connected,
        state=_current.stage_states(),
        summary=_current.summary(),
        fingerprint_hash=_current.fingerprint.fingerprint_hash if _current.fingerprint else None,
        table_count=len(_current.fingerprint.tables) if _current.fingerprint else 0,
        entities=sorted(_current.mapping.entities) if _current.mapping else [],
        mapping_path=str(_current.mapping_path) if _current.mapping_path else None,
        reconciled=_current.reconciliation.accepted if _current.reconciliation else None,
        reconciliation_failures=(
            list(_current.reconciliation.failures) if _current.reconciliation else []
        ),
        counts=dict(_current.load_report.counts) if _current.load_report else {},
    )


# --- routes ---------------------------------------------------------------------------


@router.get("/companies")
async def companies() -> dict[str, Any]:
    """The demo catalog: which tenants exist, and what each one's schema looks like.

    Built by `python -m scripts.demo_company`. An empty list is not an error — it means
    the demo databases have not been generated on this host, and the screen says so
    rather than pretending there is nothing to connect to.
    """
    if not DEMO_MANIFEST.exists():
        return {
            "available": False,
            "hint": "run `make demo-companies` to build the demo source databases",
            "companies": [],
        }
    manifest = json.loads(DEMO_MANIFEST.read_text(encoding="utf-8"))
    return {"available": True, "hint": None, **manifest}


@router.get("")
async def current() -> OnboardingStatus:
    return _status()


@router.post("/connect")
async def connect(body: ConnectRequest) -> dict[str, Any]:
    """Stage 1. Tests the credential and reports what it can do. The URL stops here."""
    global _current
    onboarding = Onboarding(
        tenant_id=body.tenant_id,
        company_name=body.company,
        default_currency=body.currency.upper(),
        schema=body.schema_name,
        operator=body.operator,
    )
    try:
        probe = onboarding.connect(body.url)
    except ConnectionError_ as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if _current is not None:
        _current.forget()
    _current = onboarding
    # A new connection means a new tenant. Everything the previous source produced --
    # cycle, agent runs, investigation, approval cards -- is dropped here rather than at
    # load, so a half-finished onboarding can never leave last tenant's numbers on screen.
    clear_session()
    return {
        "probe": {
            "dialect": probe.dialect,
            "server_version": probe.server_version,
            "schema": probe.schema,
            "can_read": probe.can_read,
            "can_write": probe.can_write,
            "write_note": probe.write_note,
            "table_count": probe.table_count,
        },
        "source": onboarding.redacted_url,
        "status": _status(),
    }


@router.post("/introspect")
async def introspect() -> dict[str, Any]:
    """Stage 2. Structure only: names, types, keys, row counts, format shapes."""
    onboarding = _require()
    try:
        fingerprint = onboarding.introspect()
    except StageError as exc:
        raise _stage_error(exc) from exc
    except ConnectionError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "fingerprint_hash": fingerprint.fingerprint_hash,
        "dialect": fingerprint.dialect,
        "tables": [
            {
                "name": table.name,
                "row_count": table.row_count,
                "columns": [
                    {
                        "name": column.name,
                        "data_type": column.data_type,
                        "nullable": column.nullable,
                        "is_pk": column.is_pk,
                        "is_fk": column.is_fk,
                        "fk_target": column.fk_target,
                        "format_signature": column.format_signature,
                    }
                    for column in table.columns
                ],
            }
            for table in fingerprint.tables
        ],
        "status": _status(),
    }


@router.post("/classify")
async def classify() -> dict[str, Any]:
    """Stages 3 and 4. The lexicon proposes; the residue is reported, not hidden."""
    onboarding = _require()
    try:
        proposal = onboarding.classify()
        mapping = onboarding.draft()
    except StageError as exc:
        raise _stage_error(exc) from exc

    return {
        "template": (
            {
                "template_id": onboarding.template.template_id,
                "score_bps": onboarding.template.score_bps,
            }
            if onboarding.template
            else None
        ),
        "model_calls": onboarding.model_calls,
        "tables": [
            {
                "table": row.table,
                "entity_role": row.entity_role,
                "confidence_bps": row.confidence_bps,
            }
            for row in proposal.tables
        ],
        "columns": [
            {
                "table": row.table,
                "column": row.column,
                "canonical_field": row.canonical_field,
                "confidence_bps": row.confidence_bps,
            }
            for row in proposal.columns
        ],
        "units": [
            {
                "table": row.table,
                "column": row.column,
                "units": row.units,
                "currency_source": row.currency_source,
                "confidence_bps": row.confidence_bps,
            }
            for row in proposal.units
        ],
        "residue": {
            "tables": list(onboarding.residue.tables) if onboarding.residue else [],
            "columns": (
                [list(pair) for pair in onboarding.residue.columns] if onboarding.residue else []
            ),
        },
        "mapping": mapping.model_dump(mode="json"),
        "status": _status(),
    }


@router.post("/load")
async def load(body: ControlTotals) -> dict[str, Any]:
    """Stages 5 to 7: read the source, write our rows, reconcile, freeze — or roll back."""
    onboarding = _require()
    stated = BalanceSnapshot(
        ar_minor=body.ar_minor,
        ap_minor=body.ap_minor,
        cash_minor=body.cash_minor,
        currency=body.currency.upper(),
    )
    try:
        with store.session() as target:
            report = onboarding.load(
                target, stated=stated, cash_tolerance_minor=body.cash_tolerance_minor
            )
            accepted = onboarding.reconciliation is not None and onboarding.reconciliation.accepted
            if accepted:
                target.commit()
            else:
                target.rollback()
    except StageError as exc:
        raise _stage_error(exc) from exc
    except LoadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    reconciliation = onboarding.reconciliation
    # A committed, reconciled load is the moment this tenant becomes *the* data source.
    # Everything on every screen from here is read back out of these rows.
    bound = await _bind(onboarding) if accepted else None
    return {
        "accepted": accepted,
        "data_source": bound.model_dump(mode="json") if bound else None,
        "committed": accepted,
        "counts": report.counts,
        "source_rows": report.source_rows,
        "mapped_rows": report.mapped_rows,
        "assumptions": sorted(set(report.assumptions)),
        "rejects": [
            {"entity": reject.entity, "source_key": reject.source_key, "reason": reject.reason}
            for reject in report.rejects[:100]
        ],
        "reject_count": len(report.rejects),
        "totals": {
            "open_invoices_minor": report.open_invoices_minor,
            "open_vendor_invoices_minor": report.open_vendor_invoices_minor,
            "bank_transactions_minor": report.bank_transactions_minor,
            "currency": report.currency,
        },
        "reconciliation": (
            {
                "accepted": reconciliation.accepted,
                "coverage_rows_bps": reconciliation.coverage_rows_bps,
                "coverage_value_bps": reconciliation.coverage_value_bps,
                "failures": list(reconciliation.failures),
            }
            if reconciliation
            else None
        ),
        "mapping_path": str(onboarding.mapping_path) if onboarding.mapping_path else None,
        "status": _status(),
    }


@router.post("/drift")
async def drift() -> dict[str, Any]:
    """Stage 8. Re-introspect and compare hashes; a changed schema holds ingestion."""
    onboarding = _require()
    try:
        report = onboarding.check_drift()
    except StageError as exc:
        raise _stage_error(exc) from exc
    except ConnectionError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "changed": report.changed,
        "previous_hash": report.previous_hash,
        "current_hash": report.current_hash,
        "hold_ingestion": report.hold_ingestion,
        "reason": report.reason,
        "status": _status(),
    }


@router.get("/ledger")
async def ledger() -> dict[str, Any]:
    """What is actually in our model now, read back from the target database.

    Reading it back rather than echoing the load report is the point: the report says
    what the loader believed it wrote, and this says what the database will admit to
    holding. When those two disagree, the second one is right.
    """
    onboarding = _require()
    tenant = onboarding.tenant_id
    with store.session() as target:
        counts = {
            name: target.exec(select(func.count()).select_from(model).where(clause)).one()
            for name, model, clause in (
                ("Customer", Customer, Customer.tenant_id == tenant),
                ("Vendor", Vendor, Vendor.tenant_id == tenant),
                ("BankAccount", BankAccount, BankAccount.tenant_id == tenant),
                ("Invoice", Invoice, Invoice.tenant_id == tenant),
                ("VendorInvoice", VendorInvoice, VendorInvoice.tenant_id == tenant),
                ("BankTransaction", BankTransaction, BankTransaction.tenant_id == tenant),
            )
        }
        open_ar = target.exec(
            select(func.coalesce(func.sum(Invoice.open_amount_minor), 0)).where(
                Invoice.tenant_id == tenant
            )
        ).one()
        open_ap = target.exec(
            select(func.coalesce(func.sum(VendorInvoice.open_amount_minor), 0)).where(
                VendorInvoice.tenant_id == tenant
            )
        ).one()
        cash = target.exec(
            select(func.coalesce(func.sum(BankTransaction.amount_minor), 0)).where(
                BankTransaction.tenant_id == tenant
            )
        ).one()
        # The five largest open receivables: the row an AR agent would reach for first,
        # and the quickest way for a POC to recognise their own book on the screen.
        largest = target.exec(
            select(Invoice, Customer.name)
            .join(Customer, Customer.id == Invoice.customer_id)
            .where(Invoice.tenant_id == tenant, Invoice.open_amount_minor > 0)
            .order_by(Invoice.open_amount_minor.desc())
            .limit(5)
        ).all()

    return {
        "tenant_id": tenant,
        "company": onboarding.company_name,
        "currency": onboarding.default_currency,
        "counts": counts,
        "totals": {
            "open_ar_minor": int(open_ar),
            "open_ap_minor": int(open_ap),
            "cash_minor": int(cash),
        },
        "largest_open_receivables": [
            {
                "invoice_ref": invoice.invoice_ref,
                "customer": customer_name,
                "open_minor": invoice.open_amount_minor,
                "currency": invoice.currency,
                "due_date": invoice.due_date.isoformat(),
                # Aged against the product's as-of date, not the wall clock: a demo run in
                # September must not report a March book as six months later.
                "days_past_due": (date.fromisoformat(AS_OF) - invoice.due_date).days,
            }
            for invoice, customer_name in largest
        ],
    }


@router.post("/reset")
async def reset() -> OnboardingStatus:
    """Discard the onboarding, the credential, and everything the tenant produced.

    The credential is the obvious part. The rest matters just as much: a reset that left
    the agent runs and the recommendation standing would leave a CFO looking at a screen
    of conclusions about a ledger that is no longer loaded.
    """
    global _current
    if _current is not None:
        _current.forget()
    _current = None
    clear_session()
    return _status()


async def _bind(onboarding: Onboarding) -> DataSource | None:
    """Point the product at the rows this onboarding just committed.

    The toolset holds its own long-lived read session on the target database, and the
    capability manifest is read *from that toolset* rather than assembled here, so the
    Data Source screen and the Commander are looking at exactly the same statement of
    what this tenant does and does not have.
    """
    opened = Session(store.engine())
    try:
        toolset = TenantToolset(
            opened,
            tenant_id=onboarding.tenant_id,
            currency=onboarding.default_currency,
        )
    except ToolError:
        # A load that reconciled but wrote nothing readable is a bug worth seeing on the
        # screen rather than a 500 on the request that reported success.
        opened.close()
        return None

    manifest = await toolset.get_capability_manifest()
    source = DataSource(
        kind="tenant",
        tenant_id=onboarding.tenant_id,
        company=onboarding.company_name,
        currency=toolset.currency,
        as_of=toolset.as_of.isoformat(),
        loaded_at=datetime.now(UTC),
        counts=toolset.counts(),
        mapping_path=str(onboarding.mapping_path) if onboarding.mapping_path else None,
        reconciled=bool(onboarding.reconciliation and onboarding.reconciliation.accepted),
        available_sources=[item.value for item in manifest.available_sources],
        missing_sources=[item.value for item in manifest.missing_sources],
        source_notes=dict(manifest.notes),
        note=(
            f"Loaded from the tenant's own database and held to its trial balance. "
            f"As-of {toolset.as_of.isoformat()} is this ledger's most recent dated row."
        ),
    )
    bind_session(
        toolset=toolset,
        data_source=source,
        company=onboarding.company_name,
        as_of=toolset.as_of.isoformat(),
    )
    return source
