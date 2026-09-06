"""The evidence validator -- where "never fabricate evidence" stops being a prompt line.

Three gates, in order. A reference must parse, it must be something a tool actually
returned during this run, and it must resolve to a real source row. A finding that fails
any of them is **rejected, not surfaced**: there is no path where a citation the ledger
cannot confirm reaches a treasurer's screen.

The validator also replaces each excerpt with the text from the resolved row. The model
does not get to paraphrase the ledger -- if the excerpt shown in the Evidence Explorer
disagreed with the source, the whole provenance chain would be worthless.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from backend.contracts.agent import AgentFinding
from backend.contracts.provenance import Evidence
from backend.tools.results import EvidenceRow
from backend.tools.toolset import ToolError, Toolset


class EvidenceRejection(BaseModel):
    """One citation that did not survive. Emitted as an `evidence.rejected` event."""

    model_config = ConfigDict(frozen=True)

    reference: str
    reason: Annotated[str, Field(min_length=1, max_length=280)]


class EvidenceVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    accepted: bool
    rejections: list[EvidenceRejection] = Field(default_factory=list)
    verified: list[Evidence] = Field(default_factory=list)

    def reason(self) -> str:
        return "; ".join(f"{r.reference}: {r.reason}" for r in self.rejections)


class EvidenceValidator:
    """Validates one agent's findings against what its tools actually returned.

    `seen` is the reference set accumulated by `ScopedToolset` during the run. An empty
    set means no tool returned anything citable, so nothing can be cited -- which is the
    correct answer, not a reason to relax the rule.
    """

    def __init__(self, toolset: Toolset, seen: set[str]) -> None:
        self._toolset = toolset
        self._seen = seen

    async def validate(self, finding: AgentFinding) -> EvidenceVerdict:
        rejections: list[EvidenceRejection] = []
        verified: list[Evidence] = []

        for evidence in finding.evidence:
            row, rejection = await self._check(evidence.reference)
            if rejection is not None:
                rejections.append(rejection)
                continue
            assert row is not None
            verified.append(
                Evidence(
                    reference=row.reference,
                    source=row.source,
                    # The excerpt comes from the ledger, never from the model.
                    excerpt=row.excerpt[:280],
                )
            )

        # A proposed action's supporting references are held to the same standard: an
        # action nobody can trace back to a row is not an action anyone can approve.
        for action in finding.recommended_actions:
            for reference in action.evidence_refs:
                _, rejection = await self._check(reference)
                if rejection is not None:
                    rejections.append(rejection)

        return EvidenceVerdict(
            accepted=not rejections and bool(verified or not finding.evidence),
            rejections=rejections,
            verified=verified,
        )

    async def _check(self, reference: str) -> tuple[EvidenceRow | None, EvidenceRejection | None]:
        if ":" not in reference or not reference.split(":", 1)[1]:
            return None, EvidenceRejection(
                reference=reference, reason="malformed reference, expected 'source:record_id'"
            )

        if reference not in self._seen:
            return None, EvidenceRejection(
                reference=reference,
                reason="not returned by any tool in this run",
            )

        try:
            row = await self._toolset.resolve_evidence(reference=reference)
        except ToolError as exc:
            return None, EvidenceRejection(reference=reference, reason=f"lookup failed: {exc}")

        if not row.resolved:
            return None, EvidenceRejection(
                reference=reference, reason="does not resolve to a source row"
            )
        return row, None


def apply_verdict(finding: AgentFinding, verdict: EvidenceVerdict) -> AgentFinding:
    """Return the finding carrying ledger-sourced excerpts. Call only on an accepted verdict."""
    if not verdict.accepted:
        raise ValueError("a rejected finding is not surfaced, so it is never rewritten")
    return finding.model_copy(update={"evidence": verdict.verified})
