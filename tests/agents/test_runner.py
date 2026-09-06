"""The run contract: gather, brief, budget, call, validate, record.

These tests are about what reaches the treasurer's screen and what does not. The evidence
cases matter most: a model that cites a plausible-looking invoice it never saw must lose
the finding, not have it quietly cleaned up.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.agents.provider import LLMTimeout
from backend.agents.runner import AgentRunner
from backend.contracts import AgentRole, AgentStatus
from backend.contracts.events import EventType
from backend.orchestrator.bus import EventBus
from backend.tools.toolset import ToolNotAllowed
from tests.agents.conftest import INVOICE_REF, StubSpec, finding_payload, recording_provider


def make_runner(bus: EventBus, toolset, routing, provider) -> AgentRunner:
    return AgentRunner(
        provider=provider,
        toolset=toolset,
        routing=routing,
        bus=bus,
        company="NovaTech Industries",
        as_of="2026-03-02",
        investigation_id="inv-1",
    )


def types_of(bus: EventBus) -> list[EventType]:
    return [event.type for event in bus.history()]


async def test_a_cited_finding_completes_and_is_recorded(
    tmp_path: Path, bus: EventBus, toolset, routing
) -> None:
    provider = recording_provider(
        tmp_path, {AgentRole.AR_COLLECTIONS: {"output": finding_payload()}}
    )
    run = await make_runner(bus, toolset, routing, provider).run(StubSpec(), run_id="run-1")

    assert run.status is AgentStatus.COMPLETE
    assert run.finding is not None
    assert run.investigation_id == "inv-1"
    assert run.tool_calls and run.tool_calls[0].tool == "rank_collection_opportunities"
    assert run.usage.total > 0
    assert run.latency_ms is not None
    assert EventType.AGENT_FINDING in types_of(bus)


async def test_the_excerpt_comes_from_the_ledger_not_the_model(
    tmp_path: Path, bus: EventBus, toolset, routing
) -> None:
    """The model may summarise. It may not restate what the source row says."""
    provider = recording_provider(
        tmp_path, {AgentRole.AR_COLLECTIONS: {"output": finding_payload()}}
    )
    run = await make_runner(bus, toolset, routing, provider).run(StubSpec(), run_id="run-1")

    assert run.finding is not None
    excerpt = run.finding.evidence[0].excerpt
    assert "paraphrase" not in excerpt
    assert "Contoso Ltd, invoice 10482" in excerpt


async def test_a_fabricated_citation_costs_the_finding(
    tmp_path: Path, bus: EventBus, toolset, routing
) -> None:
    provider = recording_provider(
        tmp_path,
        {
            AgentRole.AR_COLLECTIONS: {
                "output": finding_payload(references=["ar_ledger:INV-99999#amount_due"])
            }
        },
    )
    run = await make_runner(bus, toolset, routing, provider).run(StubSpec(), run_id="run-1")

    assert run.status is AgentStatus.DEGRADED
    assert run.finding is None, "a rejected finding is never surfaced"
    assert "not returned by any tool" in (run.failure_reason or "")
    assert EventType.EVIDENCE_REJECTED in types_of(bus)
    assert EventType.AGENT_FINDING not in types_of(bus)


async def test_a_refusal_is_an_outcome_not_a_failure(
    tmp_path: Path, bus: EventBus, toolset, routing
) -> None:
    """The AP agent asked to defer payroll refuses -- and that run is a good run."""
    provider = recording_provider(
        tmp_path,
        {
            AgentRole.AR_COLLECTIONS: {
                "output": finding_payload(
                    status=AgentStatus.REFUSED,
                    headline="Cannot accelerate: the only lever breaches a hard constraint",
                    detail="Deferring payroll is a protected payment class under policy v4.",
                )
            }
        },
    )
    run = await make_runner(bus, toolset, routing, provider).run(StubSpec(), run_id="run-1")

    assert run.status is AgentStatus.REFUSED
    assert run.finding is not None
    assert EventType.AGENT_FINDING in types_of(bus)


async def test_an_agent_cannot_file_a_finding_under_another_agents_name(
    tmp_path: Path, bus: EventBus, toolset, routing
) -> None:
    provider = recording_provider(
        tmp_path,
        {AgentRole.AR_COLLECTIONS: {"output": finding_payload(agent=AgentRole.SUPPLIER_RISK)}},
    )
    run = await make_runner(bus, toolset, routing, provider).run(StubSpec(), run_id="run-1")

    assert run.status is AgentStatus.FAILED
    assert "attributed to supplier_risk" in (run.failure_reason or "")


async def test_an_over_budget_prompt_fails_before_it_is_paid_for(
    tmp_path: Path, bus: EventBus, toolset, routing
) -> None:
    """LLM_STRATEGY section 4: a prompt change that doubles context fails, loudly."""
    provider = recording_provider(
        tmp_path, {AgentRole.AR_COLLECTIONS: {"output": finding_payload()}}
    )
    bloated = StubSpec(system_prompt="cite everything. " * 400)
    run = await make_runner(bus, toolset, routing, provider).run(bloated, run_id="run-1")

    assert run.status is AgentStatus.FAILED
    assert "system prompt" in (run.failure_reason or "")
    assert provider.requests == [], "the call was never made"


async def test_a_tool_failure_degrades_the_agent_rather_than_the_wave(
    tmp_path: Path, bus: EventBus, toolset, routing
) -> None:
    provider = recording_provider(
        tmp_path, {AgentRole.AR_COLLECTIONS: {"output": finding_payload()}}
    )
    broken = StubSpec(role=AgentRole.SUPPLIER_RISK, tools=["get_supplier_risk_profile"])
    run = await make_runner(bus, toolset, routing, provider).run(broken, run_id="run-1")

    assert run.status is AgentStatus.DEGRADED
    assert "tool layer" in (run.failure_reason or "")


async def test_reaching_outside_the_allowlist_is_a_bug_and_says_so(
    tmp_path: Path, bus: EventBus, toolset, routing
) -> None:
    provider = recording_provider(tmp_path, {AgentRole.DODO_REVENUE: {"output": finding_payload()}})
    trespassing = StubSpec(role=AgentRole.DODO_REVENUE, tools=["rank_collection_opportunities"])

    with pytest.raises(ToolNotAllowed):
        await make_runner(bus, toolset, routing, provider).run(trespassing, run_id="run-1")


async def test_a_timeout_is_left_for_the_executor_to_judge(
    tmp_path: Path, bus: EventBus, toolset, routing
) -> None:
    """The runner owns the run; the retry budget belongs one layer up."""
    provider = recording_provider(tmp_path, {AgentRole.AR_COLLECTIONS: {"raises": "timeout"}})

    with pytest.raises(LLMTimeout):
        await make_runner(bus, toolset, routing, provider).run(StubSpec(), run_id="run-1")


async def test_the_brief_carries_the_pack_and_the_tool_results(
    tmp_path: Path, bus: EventBus, toolset, routing
) -> None:
    provider = recording_provider(
        tmp_path, {AgentRole.AR_COLLECTIONS: {"output": finding_payload()}}
    )
    await make_runner(bus, toolset, routing, provider).run(StubSpec(), run_id="run-1")

    prompt = provider.requests[0].messages[0].content
    assert "NovaTech Industries" in prompt
    assert "WHAT YOUR TOOLS RETURNED" in prompt
    assert "rank_collection_opportunities" in prompt


async def test_a_prior_finding_reaches_the_next_agent_as_one_line(
    tmp_path: Path, bus: EventBus, toolset, routing, finding
) -> None:
    provider = recording_provider(
        tmp_path, {AgentRole.AR_COLLECTIONS: {"output": finding_payload()}}
    )
    await make_runner(bus, toolset, routing, provider).run(
        StubSpec(), run_id="run-1", prior_findings=[finding]
    )

    prompt = provider.requests[0].messages[0].content
    assert "PRIOR FINDINGS" in prompt
    assert finding.headline in prompt
    assert INVOICE_REF not in prompt.split("PRIOR FINDINGS")[1].split("TASK")[0], (
        "the digest carries conclusions, never another agent's evidence"
    )
