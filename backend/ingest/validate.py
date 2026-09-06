"""Reconciliation gate for proposed schema mappings.

A mapping is accepted only if AR, AP, cash and GL tie out. Wrong mappings fail
arithmetically — that is what makes it safe for a small model to propose one.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.ingest.mapping import TenantMapping


@dataclass(frozen=True, slots=True)
class BalanceSnapshot:
    """Tenant-stated control balances used as the reconciliation target."""

    ar_minor: int
    ap_minor: int
    cash_minor: int
    currency: str = "USD"
    gl_debits_minor: int | None = None
    gl_credits_minor: int | None = None


@dataclass(frozen=True, slots=True)
class MappedTotals:
    open_invoices_minor: int
    open_vendor_invoices_minor: int
    bank_transactions_minor: int
    mapped_rows: int
    source_rows: int
    mapped_value_minor: int
    source_value_minor: int


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    accepted: bool
    coverage_rows_bps: int
    coverage_value_bps: int
    failures: tuple[str, ...]


def reconcile(
    mapping: TenantMapping,
    *,
    stated: BalanceSnapshot,
    mapped: MappedTotals,
    cash_tolerance_minor: int = 0,
) -> ReconciliationResult:
    """Deterministic gate. Returns structured failures, never a soft warning."""
    del mapping  # mapping identity is audited by the caller; gate is arithmetic.
    failures: list[str] = []
    if mapped.open_invoices_minor != stated.ar_minor:
        failures.append(
            f"AR mismatch: mapped {mapped.open_invoices_minor} != stated {stated.ar_minor}"
        )
    if mapped.open_vendor_invoices_minor != stated.ap_minor:
        failures.append(
            f"AP mismatch: mapped {mapped.open_vendor_invoices_minor} != stated {stated.ap_minor}"
        )
    cash_delta = abs(mapped.bank_transactions_minor - stated.cash_minor)
    if cash_delta > cash_tolerance_minor:
        failures.append(
            f"cash mismatch: mapped {mapped.bank_transactions_minor} != stated {stated.cash_minor}"
        )
    if (
        stated.gl_debits_minor is not None
        and stated.gl_credits_minor is not None
        and stated.gl_debits_minor != stated.gl_credits_minor
    ):
        failures.append(
            f"GL imbalance: debits {stated.gl_debits_minor} != credits {stated.gl_credits_minor}"
        )

    rows_bps = (
        0
        if mapped.source_rows == 0
        else min(10_000, (10_000 * mapped.mapped_rows) // mapped.source_rows)
    )
    value_bps = (
        0
        if mapped.source_value_minor == 0
        else min(10_000, (10_000 * mapped.mapped_value_minor) // mapped.source_value_minor)
    )
    return ReconciliationResult(
        accepted=not failures,
        coverage_rows_bps=rows_bps,
        coverage_value_bps=value_bps,
        failures=tuple(failures),
    )


__all__ = [
    "BalanceSnapshot",
    "MappedTotals",
    "ReconciliationResult",
    "reconcile",
]
