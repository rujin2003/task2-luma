"""The Context Pack: deterministic, bounded, and honest about what the tenant cannot see."""

from __future__ import annotations

import pytest

from backend.agents.context import Incident, assemble
from backend.agents.routing import load_routing
from backend.contracts import AgentFinding, AgentRole, AgentStatus, Evidence, Money
from backend.contracts.provenance import SourceSystem
from backend.tools.fixtures import FixtureToolset

BUDGET = load_routing().budgets
INCIDENT = Incident(
    trigger="minimum cash $18.4M against a $20.0M floor",
    detected_on="2026-03-02",
    detail="W6 closing cash breaches the policy floor by $1.6M",
)


def pack(**overrides):
    kwargs = {
        "agent": AgentRole.VARIANCE,
        "company": "NovaTech Industries",
        "as_of": "2026-03-02",
        "task": "Root-cause the material deltas in the W09 bridge.",
        "tools": ["get_variance_bridge", "get_liquidity_position"],
        "output_schema": "AgentFinding",
        "budget": BUDGET,
    }
    return assemble(**{**kwargs, **overrides})


def digest_finding(index: int) -> AgentFinding:
    return AgentFinding(
        agent=AgentRole.AR_COLLECTIONS,
        status=AgentStatus.COMPLETE,
        headline=f"Finding {index}: enterprise AR slipped by a further tranche",
        quantum=Money.from_major("1400000", "USD"),
        evidence=[
            Evidence(
                reference="ar_ledger:INV-10482#amount_due",
                source=SourceSystem.AR_LEDGER,
                excerpt="Invoice 10482, $1.2M, 41 days past terms",
            )
        ],
    )


def test_the_same_inputs_render_the_same_brief() -> None:
    """Replay recordings are keyed on the request, so drift here silently breaks them."""
    assert pack().render() == pack().render()


def test_the_brief_states_the_incident_and_the_task() -> None:
    rendered = pack(incident=INCIDENT).render()

    assert "NovaTech Industries, as of 2026-03-02" in rendered
    assert "minimum cash $18.4M" in rendered
    assert "Root-cause the material deltas" in rendered
    assert "Return one AgentFinding" in rendered


async def test_the_brief_names_what_this_tenant_cannot_see() -> None:
    """An agent that is not told a source is missing will infer from its silence."""
    capabilities = await FixtureToolset().get_capability_manifest()
    rendered = pack(capabilities=capabilities).render()

    assert "available:" in rendered
    if capabilities.missing_sources:
        assert "not available:" in rendered


async def test_a_floor_breach_is_stated_rather_than_left_to_arithmetic() -> None:
    position = await FixtureToolset().get_liquidity_position()
    rendered = pack(position=position).render()

    assert "cash today" in rendered
    if position.breaches_floor:
        assert "breaches the policy floor" in rendered


def test_the_digest_carries_conclusions_not_other_agents_evidence() -> None:
    rendered = pack(prior_findings=[digest_finding(1)]).render()

    assert "ar_collections ok" in rendered
    assert "Finding 1" in rendered
    assert "INV-10482" not in rendered


def test_the_digest_is_trimmed_oldest_first_until_the_pack_fits() -> None:
    """Cross-agent context grows linearly and slowly, never quadratically."""
    many = [digest_finding(index) for index in range(200)]
    built = pack(prior_findings=many)

    assert built.tokens <= BUDGET.context_pack
    assert built.digest_lines, "trimming stops at the budget, it does not empty the digest"
    assert "Finding 0" not in built.render(), "the oldest conclusions go first"


def test_a_pack_without_a_task_is_not_a_pack() -> None:
    with pytest.raises(ValueError):
        pack(task="")
