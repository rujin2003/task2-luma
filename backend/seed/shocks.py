"""Deterministic, reversible anomaly packs for the NovaTech demo."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from backend.models import (
    Assumption,
    BankAccount,
    DebtFacility,
    ForecastVersion,
    Invoice,
    Subscription,
    VendorInvoice,
)


@dataclass(frozen=True, slots=True)
class _Change:
    model: type[Any]
    row_id: str
    field: str
    before: object


@dataclass(frozen=True, slots=True)
class ShockToken:
    """The exact before-images needed to reverse one applied shock."""

    name: str
    changes: tuple[_Change, ...]

    def rollback(self, session: Session) -> None:
        for change in reversed(self.changes):
            row = session.get(change.model, change.row_id)
            if row is None:
                raise LookupError(f"{self.name}: missing row {change.row_id} during rollback")
            setattr(row, change.field, change.before)
            session.add(row)
        session.flush()


def _set(rows: list[Any], field: str, value: object, name: str, session: Session) -> ShockToken:
    changes: list[_Change] = []
    for row in rows:
        changes.append(_Change(type(row), row.id, field, getattr(row, field)))
        setattr(row, field, value)
        session.add(row)
    session.flush()
    return ShockToken(name, tuple(changes))


def customer_delay_4m(session: Session, company_id: str) -> ShockToken:
    rows = list(
        session.exec(
            select(Invoice)
            .where(Invoice.company_id == company_id, Invoice.open_amount_minor > 0)
            .order_by(Invoice.invoice_ref)
            .limit(4)
        ).all()
    )
    changes: list[_Change] = []
    for row in rows:
        changes.append(_Change(Invoice, row.id, "due_date", row.due_date))
        row.due_date += timedelta(days=30)
        session.add(row)
    session.flush()
    return ShockToken("customer_delay_4m", tuple(changes))


def dodo_success_rate_drop(session: Session, company_id: str) -> ShockToken:
    rows = list(
        session.exec(
            select(Subscription)
            .where(Subscription.company_id == company_id, Subscription.status == "active")
            .order_by(Subscription.external_id)
            .limit(8)
        ).all()
    )
    return _set(rows, "status", "past_due", "dodo_success_rate_drop", session)


def supplier_acceleration_1m(session: Session, company_id: str) -> ShockToken:
    rows = list(
        session.exec(
            select(VendorInvoice)
            .where(VendorInvoice.company_id == company_id, VendorInvoice.open_amount_minor > 0)
            .order_by(VendorInvoice.invoice_ref)
            .limit(6)
        ).all()
    )
    changes: list[_Change] = []
    for row in rows:
        changes.append(_Change(VendorInvoice, row.id, "due_date", row.due_date))
        row.due_date = max(row.issued_date, row.due_date - timedelta(days=30))
        session.add(row)
    session.flush()
    return ShockToken("supplier_acceleration_1m", tuple(changes))


def bank_gl_variance(session: Session, company_id: str) -> ShockToken:
    account = session.exec(
        select(BankAccount).where(
            BankAccount.company_id == company_id, BankAccount.account_ref == "op-4471"
        )
    ).one()
    return _set(
        [account],
        "current_balance_minor",
        account.current_balance_minor - 2_500_000,
        "bank_gl_variance",
        session,
    )


def covenant_headroom_squeeze(session: Session, company_id: str) -> ShockToken:
    facility = session.exec(
        select(DebtFacility).where(
            DebtFacility.company_id == company_id, DebtFacility.name == "Revolving Credit Facility"
        )
    ).one()
    squeezed = min(facility.limit_minor, facility.drawn_minor + 1_500_000_000)
    return _set([facility], "drawn_minor", squeezed, "covenant_headroom_squeeze", session)


def stale_forecast_assumption(session: Session, company_id: str) -> ShockToken:
    row = session.exec(
        select(Assumption)
        .join(ForecastVersion, ForecastVersion.id == Assumption.version_id)
        .where(ForecastVersion.company_id == company_id)
        .order_by(Assumption.as_of.desc(), Assumption.category)
        .limit(1)
    ).one()
    old_day = date(2025, 1, 1)
    changes = (
        _Change(Assumption, row.id, "as_of", row.as_of),
        _Change(Assumption, row.id, "review_due", row.review_due),
    )
    row.as_of = old_day
    row.review_due = old_day + timedelta(days=14)
    session.add(row)
    session.flush()
    return ShockToken("stale_forecast_assumption", changes)


SHOCKS = {
    "customer_delay_4m": customer_delay_4m,
    "dodo_success_rate_drop": dodo_success_rate_drop,
    "supplier_acceleration_1m": supplier_acceleration_1m,
    "bank_gl_variance": bank_gl_variance,
    "covenant_headroom_squeeze": covenant_headroom_squeeze,
    "stale_forecast_assumption": stale_forecast_assumption,
}


def apply(session: Session, name: str, company_id: str) -> ShockToken:
    try:
        shock = SHOCKS[name]
    except KeyError as exc:
        raise KeyError(f"unknown shock {name!r}; choose from {sorted(SHOCKS)}") from exc
    return shock(session, company_id)


def rollback(session: Session, token: ShockToken) -> None:
    token.rollback(session)


__all__ = [
    "SHOCKS",
    "ShockToken",
    "apply",
    "bank_gl_variance",
    "covenant_headroom_squeeze",
    "customer_delay_4m",
    "dodo_success_rate_drop",
    "rollback",
    "stale_forecast_assumption",
    "supplier_acceleration_1m",
]
