"""The evidence validator: three gates, and no way past them.

A reference must parse, it must be something a tool actually returned during this run, and
it must resolve to a real row. These tests exist because "never fabricate evidence" is a
property of this module or it is a property of nothing.
"""

from __future__ import annotations

import pytest

from backend.agents.evidence import EvidenceValidator, apply_verdict
from backend.contracts import AgentFinding, AgentRole, AgentStatus, Evidence
from backend.contracts.agent import ActionKind, ProposedAction
from backend.contracts.provenance import SourceSystem
from backend.tools.fixtures import FixtureToolset
from tests.agents.conftest import INVOICE_REF

MODEL_EXCERPT = "a paraphrase the model would like to show the treasurer"


def cited(*references: str, **extra) -> AgentFinding:
    return AgentFinding(
        agent=AgentRole.AR_COLLECTIONS,
        status=AgentStatus.COMPLETE,
        headline="$2.6M of AR is realistically accelerable",
        evidence=[
            Evidence(
                reference=reference,
                source=SourceSystem(reference.split(":", 1)[0]),
                excerpt=MODEL_EXCERPT,
            )
            for reference in references
        ],
        **extra,
    )


def validator(seen: set[str]) -> EvidenceValidator:
    return EvidenceValidator(FixtureToolset(), seen)


async def test_a_citation_the_tools_returned_and_the_ledger_confirms_is_accepted() -> None:
    verdict = await validator({INVOICE_REF}).validate(cited(INVOICE_REF))

    assert verdict.accepted
    assert not verdict.rejections
    assert verdict.verified[0].reference == INVOICE_REF


async def test_the_verified_excerpt_replaces_the_models_wording() -> None:
    verdict = await validator({INVOICE_REF}).validate(cited(INVOICE_REF))
    finding = apply_verdict(cited(INVOICE_REF), verdict)

    assert finding.evidence[0].excerpt != MODEL_EXCERPT
    assert "invoice 10482" in finding.evidence[0].excerpt


async def test_a_reference_no_tool_returned_is_rejected() -> None:
    """The classic failure: a plausible invoice number the model never actually saw."""
    verdict = await validator(set()).validate(cited(INVOICE_REF))

    assert not verdict.accepted
    assert "not returned by any tool" in verdict.reason()


async def test_a_malformed_reference_is_rejected_before_it_is_looked_up() -> None:
    verdict = await validator({"INV-10482"}).validate(cited("ar_ledger:"))

    assert not verdict.accepted
    assert "malformed reference" in verdict.reason()


async def test_a_reference_that_resolves_to_nothing_is_rejected() -> None:
    ghost = "ar_ledger:INV-00000#amount_due"
    verdict = await validator({ghost}).validate(cited(ghost))

    assert not verdict.accepted
    assert "does not resolve" in verdict.reason()


async def test_one_bad_citation_costs_the_whole_finding() -> None:
    """Partial credit would let a fabricated number ride alongside a real one."""
    ghost = "ar_ledger:INV-00000#amount_due"
    verdict = await validator({INVOICE_REF, ghost}).validate(cited(INVOICE_REF, ghost))

    assert not verdict.accepted
    assert len(verdict.rejections) == 1


async def test_a_proposed_action_is_held_to_the_same_standard() -> None:
    """An action nobody can trace to a row is not an action anyone can approve."""
    finding = cited(
        INVOICE_REF,
        recommended_actions=[
            ProposedAction(
                kind=ActionKind.COLLECTION_CALL,
                rationale="Chase the disputed delivery note before it ages further",
                evidence_refs=["ar_ledger:INV-00000#amount_due"],
            )
        ],
    )
    verdict = await validator({INVOICE_REF}).validate(finding)

    assert not verdict.accepted
    assert "not returned by any tool" in verdict.reason()


async def test_a_rejected_finding_is_never_rewritten() -> None:
    verdict = await validator(set()).validate(cited(INVOICE_REF))

    with pytest.raises(ValueError, match="never rewritten"):
        apply_verdict(cited(INVOICE_REF), verdict)
