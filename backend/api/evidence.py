"""Evidence resolution: the endpoint behind "click any number, walk to the source row".

The reference arrives as a query parameter rather than a path segment for a boring but
real reason: references are spelled `source:record_id#field`, and `#` cannot survive a URL
path -- a browser would treat it as a fragment and never send it. A query parameter
carries it intact.

The important behaviour is the negative one. A reference that does not resolve returns
`resolved: false` with a 200, not a 404. The Evidence Explorer draws an unresolvable
citation as a visible break in the chain, and it can only do that if it gets an answer to
render. A 404 would collapse "this number cites a row that has gone missing" into "this
URL is wrong", and those are very different problems for an analyst to be looking at.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from backend.api.session import Session, get_session
from backend.contracts.provenance import SourceSystem
from backend.tools.results import EvidenceRow
from backend.tools.toolset import ToolError

router = APIRouter(prefix="/api/evidence", tags=["evidence"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("")
async def resolve(
    session: SessionDep,
    reference: Annotated[str, Query(min_length=1, description="source:record_id#field")],
) -> EvidenceRow:
    """One source row, or an honest unresolved marker for the UI to draw as a gap."""
    try:
        row = await session.toolset.resolve_evidence(reference=reference)
    except ToolError as exc:
        return _gap(reference, str(exc))
    if not row.resolved and not row.excerpt:
        # The toolset returns an unresolved row with an empty excerpt, which is right for
        # the evidence validator -- it only needs the boolean. A human reading the drawer
        # needs a sentence, so the gap is given one here rather than rendering as blank.
        return _gap(reference, f"{reference} does not resolve to a row in the source ledger")
    return row


def _gap(reference: str, why: str) -> EvidenceRow:
    return EvidenceRow(
        reference=reference,
        source=_source_of(reference),
        excerpt=why,
        resolved=False,
    )


def _source_of(reference: str) -> SourceSystem:
    """Best effort from the reference itself; `forecast` is the honest fallback."""
    prefix = reference.split(":", 1)[0]
    try:
        return SourceSystem(prefix)
    except ValueError:
        return SourceSystem.FORECAST
