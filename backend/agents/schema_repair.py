"""Repair LLM payloads that fail AgentFinding's conditional validators.

Gemini's `responseSchema` cannot express rules like "complete requires evidence" or
"empirical confidence requires mape_pct". The model is told those rules in the prompt,
but it still sometimes returns a schema-valid JSON object that fails the Pydantic
validators — and that used to kill the whole run as a SchemaViolation.

These repairs are deliberately narrow and never invent citations: an uncitable
`complete` finding becomes `degraded`; over-long excerpts are truncated; a broken
confidence block is downgraded to a qualitative judgement. Fabrication stays
impossible because we never add evidence references here.
"""

from __future__ import annotations

import copy
from typing import Any


def repair_finding_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *payload* with known Gemini schema-gap slips coerced."""
    out = copy.deepcopy(payload)

    evidence = out.get("evidence")
    if isinstance(evidence, list):
        for item in evidence:
            if not isinstance(item, dict):
                continue
            excerpt = item.get("excerpt")
            if isinstance(excerpt, str) and len(excerpt) > 280:
                item["excerpt"] = excerpt[:280]

    if out.get("status") == "complete" and not evidence:
        out["status"] = "degraded"
        detail = out.get("detail") if isinstance(out.get("detail"), str) else ""
        note = "Returned without citations; marked degraded."
        out["detail"] = f"{detail} {note}".strip()[:1200]

    # A rejection without citations is a hunch — drop it rather than inventing evidence.
    if out.get("rejects") and not evidence:
        out["rejects"] = []

    if out.get("requires_followup") and not out.get("followup_question"):
        out["requires_followup"] = False

    for key, limit in (("headline", 200), ("detail", 1200), ("followup_question", 280)):
        value = out.get(key)
        if isinstance(value, str) and len(value) > limit:
            out[key] = value[:limit]

    risks = out.get("risks")
    if isinstance(risks, list):
        out["risks"] = [risk[:280] if isinstance(risk, str) else risk for risk in risks]

    confidence = out.get("confidence")
    if isinstance(confidence, dict):
        rationale = confidence.get("rationale")
        if isinstance(rationale, str) and len(rationale) > 280:
            confidence["rationale"] = rationale[:280]

        basis = confidence.get("basis")
        if basis == "empirical" and (
            confidence.get("mape_pct") is None or confidence.get("sample_size") is None
        ):
            confidence["basis"] = "qualitative"
            confidence["band"] = confidence.get("band") or "medium"
            confidence.pop("mape_pct", None)
            confidence.pop("sample_size", None)
            confidence.pop("horizon_weeks", None)
        elif basis == "qualitative" and not confidence.get("band"):
            confidence["band"] = "medium"
        elif basis == "empirical" and confidence.get("band") is not None:
            confidence.pop("band", None)

    return out
