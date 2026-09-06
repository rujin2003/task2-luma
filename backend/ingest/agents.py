"""Cartographer agent stage boundary.

Stage 3's four sub-agents run on Person 2's runtime. Person 1 supplies the
input profiles and consumes the structured output. No raw financial rows are
ever included in these payloads.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from backend.ingest.fingerprint import SchemaFingerprint, TableProfile


class TableClassification(BaseModel):
    model_config = ConfigDict(frozen=True)

    table: str
    entity_role: str
    confidence_bps: int = Field(ge=0, le=10_000)


class ColumnMapping(BaseModel):
    model_config = ConfigDict(frozen=True)

    table: str
    column: str
    canonical_field: str
    confidence_bps: int = Field(ge=0, le=10_000)


class AccountRole(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_number: str
    account_name: str
    role: str
    confidence_bps: int = Field(ge=0, le=10_000)


class UnitCurrencyFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    table: str
    column: str
    units: str  # minor | major
    currency_source: str  # column | implied | tenant_default
    confidence_bps: int = Field(ge=0, le=10_000)


class CartographerProposal(BaseModel):
    model_config = ConfigDict(frozen=True)

    tables: tuple[TableClassification, ...] = ()
    columns: tuple[ColumnMapping, ...] = ()
    account_roles: tuple[AccountRole, ...] = ()
    units: tuple[UnitCurrencyFinding, ...] = ()


class CartographerAgents(Protocol):
    """Person 2 implements this over their agent runtime."""

    def propose(
        self, fingerprint: SchemaFingerprint, residue: tuple[TableProfile, ...]
    ) -> CartographerProposal: ...


class NullCartographerAgents:
    """Deterministic no-op used when residue is empty or agents are unavailable."""

    def propose(
        self, fingerprint: SchemaFingerprint, residue: tuple[TableProfile, ...]
    ) -> CartographerProposal:
        del fingerprint, residue
        return CartographerProposal()


__all__ = [
    "AccountRole",
    "CartographerAgents",
    "CartographerProposal",
    "ColumnMapping",
    "NullCartographerAgents",
    "TableClassification",
    "UnitCurrencyFinding",
]
