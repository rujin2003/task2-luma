from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.finance import Provenance


def test_provenance_frozen_and_required() -> None:
    as_of = datetime(2026, 8, 30, tzinfo=UTC)
    retrieved = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    provenance = Provenance(
        source_system="erp",
        source_table="invoices",
        source_pk="INV-001832",
        field="amount_cents",
        as_of=as_of,
        retrieved_at=retrieved,
    )
    assert provenance.source_pk == "INV-001832"
    with pytest.raises(ValidationError):
        provenance.source_pk = "mutated"  # type: ignore[misc]


def test_provenance_rejects_blank_fields() -> None:
    as_of = datetime(2026, 8, 30, tzinfo=UTC)
    retrieved = datetime(2026, 9, 1, tzinfo=UTC)
    with pytest.raises(ValidationError):
        Provenance(
            source_system="  ",
            source_table="invoices",
            source_pk="1",
            field="amount",
            as_of=as_of,
            retrieved_at=retrieved,
        )
