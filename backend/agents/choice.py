"""A constrained structured choice, for the roles that select rather than explain.

The Commander and the Stress Test agent do not produce findings. They pick: which
investigation plan, which stressors. `LLM_STRATEGY.md` section 6 is explicit about why
that distinction matters -- a small model asked to plan freely produces something
plausible and unrepeatable, while the same model asked to choose one of five enumerated
plans and say why is reliable and costs nothing extra.

So this is deliberately not `AgentRunner`. It shares everything that earns its place --
the scoped toolset and its allowlist, the Context Pack, the token-budget gate, the tool
call events -- and drops the two steps that only make sense for a finding: the evidence
validator and the `AgentRun` record. A plan choice cites no ledger rows, so validating it
against them would be theatre.

`choose` never raises for a model failure. It returns `None` and the reason, because
every caller has a deterministic fallback and a Commander that crashed the investigation
because the model was rate-limited would be a worse Commander than one that took the
default plan and said so.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from backend.agents.context import Incident, assemble
from backend.agents.provider import LLMError, LLMProvider, LLMRequest, Msg
from backend.agents.routing import ModelRouting
from backend.agents.runner import BudgetExceeded, assert_within_budget, fit_to_budget
from backend.contracts.agent import AgentFinding, AgentRole, AgentStatus
from backend.contracts.events import (
    AgentStarted,
    AgentStatusChanged,
    AgentToolCall,
    StatusMark,
)
from backend.orchestrator.bus import EventBus
from backend.tools.registry import ScopedToolset, tools_for


@dataclass(slots=True)
class Choice[ChoiceT: BaseModel]:
    """What the model picked, or why it did not get to pick."""

    value: ChoiceT | None
    failure_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def ok(self) -> bool:
        return self.value is not None


async def choose[ChoiceT: BaseModel](
    schema: type[ChoiceT],
    *,
    role: AgentRole,
    system_prompt: str,
    task: str,
    brief_lines: list[str],
    provider: LLMProvider,
    routing: ModelRouting,
    bus: EventBus,
    company: str,
    as_of: str,
    scoped: ScopedToolset | None = None,
    incident: Incident | None = None,
    investigation_id: str | None = None,
    prior_findings: list[AgentFinding] | None = None,
    run_id: str | None = None,
) -> Choice[ChoiceT]:
    """Assemble a brief, spend it under budget, and return the model's selection."""
    route = routing.route(role)
    budget = routing.budgets

    bus.emit(
        AgentStarted,
        investigation_id=investigation_id,
        status_line=f"{role.value}: working",
        agent=role,
        run_id=run_id or role.value,
        model=route.model,
    )
    if scoped is not None:
        for call in scoped.calls:
            bus.emit(
                AgentToolCall,
                investigation_id=investigation_id,
                mark=StatusMark.FAIL if call.error else StatusMark.WORKING,
                status_line=f"{role.value}: {call.tool}",
                agent=role,
                run_id=run_id or role.value,
                tool=call.tool,
                duration_ms=max(call.duration_ms, 0),
                row_count=call.row_count,
                truncated_from=call.truncated_from,
                error=call.error[:280] if call.error else None,
            )

    pack = assemble(
        agent=role,
        company=company,
        as_of=as_of,
        task=task,
        tools=sorted(tools_for(role)),
        output_schema=schema.__name__,
        budget=budget,
        incident=incident,
        prior_findings=prior_findings,
    )
    lines = fit_to_budget(brief_lines, budget.tool_results)
    rendered = "\n".join(f"- {line}" for line in lines)
    request = LLMRequest(
        agent=role,
        model=route.model,
        system=system_prompt,
        messages=[
            Msg(role="user", content=f"{pack.render()}\n\nWHAT YOU MAY CHOOSE FROM\n{rendered}")
        ],
        max_output_tokens=route.max_output_tokens,
        temperature=route.temperature,
    )

    def settle(choice: Choice[ChoiceT]) -> Choice[ChoiceT]:
        """Close the lane this call opened, on every path out of here.

        Without this a chooser emits `agent.started` and never a terminal status, so the
        War Room draws the Commander as still working on an investigation that closed
        minutes ago. `degraded` rather than `failed` on the unhappy path is the accurate
        word: the caller has a deterministic fallback and the investigation continues with
        a real answer, it just did not get the model's opinion.
        """
        bus.emit(
            AgentStatusChanged,
            investigation_id=investigation_id,
            mark=StatusMark.OK if choice.ok else StatusMark.WARN,
            status_line=f"{role.value}: {'chose' if choice.ok else 'fell back'}",
            agent=role,
            run_id=run_id or role.value,
            status=AgentStatus.COMPLETE if choice.ok else AgentStatus.DEGRADED,
            failure_reason=choice.failure_reason or None,
        )
        return choice

    try:
        assert_within_budget(request, budget.system_prompt, budget.total_input)
    except BudgetExceeded as exc:
        return settle(Choice(None, failure_reason=str(exc)))

    try:
        value, usage = await provider.complete(request, schema, timeout_s=route.timeout_s)
    except LLMError as exc:
        # Includes the timeout. There is no retry here: the caller's fallback is a real
        # answer, and spending the tier's rate limit on a second attempt at a choice we
        # can make deterministically is the wrong trade.
        return settle(Choice(None, failure_reason=f"{type(exc).__name__}: {exc}"))

    return settle(Choice(value, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens))
