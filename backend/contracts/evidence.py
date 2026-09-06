"""Evidence a finding cites. `reference` must resolve to a real row."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from backend.contracts.common import FrozenModel
from backend.finance.provenance import Provenance


class Evidence(FrozenModel):
    source: str = Field(..., min_length=1)
    reference: str = Field(..., min_length=1)
    provenance: Provenance
    kind: Literal["row", "calculation", "assumption", "policy"] = "row"
