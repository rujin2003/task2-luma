"""Shared column mixins and model-layer conventions.

Conventions this module fixes for the whole data model — everything downstream
(seeding, forecasting, the reconciliation gate) depends on them holding:

* **Money** is always a `BIGINT` minor-unit integer paired with a `CHAR(3)`
  ISO-4217 code. Never one without the other; `money_pair` enforces it.
* **Sign convention.** Signed columns are stated per table. Cash movements
  (`BankTransaction`, `ForecastLine`) are signed with *positive = cash in*.
  Document amounts (invoices, payments, payment runs) are non-negative
  magnitudes whose direction is implied by the entity. GL lines carry separate
  non-negative debit and credit columns.
* **Identity.** Seeded rows use `derived_id`, a UUIDv5 over a fixed namespace, so
  a given seed produces byte-identical primary keys on every run. `new_id` is
  for rows created at runtime, where a random key is correct.
* **Bitemporality.** `effective_at` is when the fact was true; `recorded_at` is
  when we learned it; `recorded_to` closes a superseded belief. `recorded_to IS
  NULL` means "still what we believe". Without the closing column an as-of query
  cannot be written, which is why it is here rather than left implicit.
* **Provenance.** `Sourced` tables carry the origin triple and a uniqueness
  constraint over it, which is what makes ingestion replay idempotent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

from sqlalchemy import CHAR, BigInteger, CheckConstraint, UniqueConstraint
from sqlmodel import Field, SQLModel

from backend.finance.currency import known_codes

# Fixed namespace for derived identifiers. Changing this value changes every
# seeded primary key, so it is a constant, never configuration.
ID_NAMESPACE = UUID("1b0f0f52-9f4a-5c2e-9a1a-7f6d3c8e2b11")

_CURRENCY_LIST = ", ".join(f"'{code}'" for code in sorted(known_codes()))


def new_id() -> str:
    """Random identifier, for rows created at runtime."""
    return uuid4().hex


def derived_id(kind: str, *parts: str) -> str:
    """Deterministic identifier from a natural key.

    Seeding must be reproducible byte-for-byte (`make seed SEED=42` twice gives
    identical dumps), which random primary keys make impossible. Every seeded
    row derives its id from its natural key instead.
    """
    if not parts:
        raise ValueError("derived_id needs at least one natural-key part")
    return uuid5(ID_NAMESPACE, "|".join((kind, *parts))).hex


def utcnow() -> datetime:
    return datetime.now(UTC)


def money_pair(amount: str, currency: str, name: str) -> CheckConstraint:
    """Amount and currency are set together or not at all."""
    return CheckConstraint(
        f"({amount} IS NULL) = ({currency} IS NULL)",
        name=f"ck_{name}_money_paired",
    )


def valid_currency(column: str, name: str) -> CheckConstraint:
    """Currency codes are restricted to the supported ISO-4217 registry."""
    return CheckConstraint(
        f"{column} IS NULL OR {column} IN ({_CURRENCY_LIST})",
        name=f"ck_{name}_{column}_iso4217",
    )


def non_negative(column: str, name: str) -> CheckConstraint:
    return CheckConstraint(f"{column} IS NULL OR {column} >= 0", name=f"ck_{name}_{column}_nonneg")


def one_of(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    """Constrain a status/kind column to a closed vocabulary."""
    rendered = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(
        f"{column} IS NULL OR {column} IN ({rendered})",
        name=f"ck_{name}_{column}_vocab",
    )


def source_unique(name: str) -> UniqueConstraint:
    """Ingestion idempotency: one row per origin record, per tenant.

    Phase 4 replays Dodo webhooks and backfills; without this, replay duplicates
    rows silently. It belongs here rather than in Phase 4 because adding it later
    means a migration over already-seeded data.
    """
    return UniqueConstraint(
        "tenant_id",
        "source_system",
        "source_table",
        "source_pk",
        name=f"uq_{name}_source",
    )


class TenantOwned(SQLModel):
    """Tenancy and demo-data provenance.

    `is_synthetic` has no default on purpose: a forgotten flag must fail loudly
    rather than silently mislabel production data as demo data, or the reverse.
    """

    tenant_id: str = Field(index=True, max_length=64)
    is_synthetic: bool


class Bitemporal(SQLModel):
    """What we knew (`recorded_at`) versus when it was true (`effective_at`).

    `recorded_to` closes a superseded belief; NULL means current. An as-of query
    is `recorded_at <= :t AND (recorded_to IS NULL OR recorded_to > :t)`.
    """

    effective_at: datetime
    recorded_at: datetime = Field(default_factory=utcnow, index=True)
    recorded_to: datetime | None = Field(default=None, index=True)


class Sourced(SQLModel):
    """Bottom of the Evidence Explorer provenance tree."""

    source_system: str = Field(max_length=64)
    source_table: str = Field(max_length=128)
    source_pk: str = Field(max_length=128)


class MoneyColumns(SQLModel):
    amount_minor: int = Field(sa_type=BigInteger)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
