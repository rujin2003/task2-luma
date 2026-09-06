"""One agent, one run: gather -> brief -> call -> review -> validate -> record.

Everything an agent shares with every other agent lives here, so a specialist is a prompt,
a tool allowlist and a `gather()` -- nothing else. That is what keeps six agents from
becoming six subtly different runtimes.

The order below is the whole contract, and each step is load-bearing:

1. **Gather.** The agent calls its allowlisted tools. The engine computes; the agent asks.
2. **Brief.** A Context Pack is assembled by code and trimmed to budget.
3. **Budget gate.** An over-budget call fails loudly here, before it is paid for.
4. **Call.** Structured output only, under a per-agent timeout.
5. **Review.** A deterministic policy gate on the answer, before it is validated.
6. **Validate.** Every citation must resolve to a row the tools actually returned, or the
   finding is rejected rather than surfaced -- the run degrades, it does not lie.
7. **Record.** An `AgentRun` is produced for every outcome, including the failures.

The runner never raises for an agent-level failure. A wave of nine agents where one fails
is a degraded wave, not a crashed one. The single exception is `LLMTimeout`, which
propagates because the executor -- not the runner -- owns the retry budget.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol

from backend.agents.context import Incident, assemble
from backend.agents.evidence import EvidenceValidator, apply_verdict
from backend.agents.provider import (
    LLMError,
    LLMProvider,
    LLMRequest,
    LLMTimeout,
    Msg,
    estimate_tokens,
)
from backend.agents.routing import ModelRouting
from backend.contracts.agent import AgentFinding, AgentRole, AgentRun, AgentStatus, TokenUsage
from backend.contracts.events import (
    STATUS_TO_MARK,
    AgentFindingEmitted,
    AgentStarted,
    AgentStatusChanged,
    AgentToolCall,
    EvidenceRejected,
    StatusMark,
)
from backend.orchestrator.bus import EventBus
from backend.tools.registry import ScopedToolset, tools_for
from backend.tools.toolset import ToolError, ToolNotAllowed, Toolset

FINDING_SCHEMA_NAME = "AgentFinding"

# Statuses an agent may choose for itself. It may not claim a timeout or a failure --
# those are the runtime's verdict on the agent, not the agent's verdict on the world.
AGENT_DECIDED = frozenset({AgentStatus.REFUSED, AgentStatus.DEGRADED})


class BudgetExceeded(LLMError):
    """The assembled call is over budget. A defect in a prompt, not a model failure."""


class AgentSpec(Protocol):
    """A specialist. Prompt, task, and the tool calls it makes before it thinks.

    `gather` returns brief lines -- already-computed decisions, one per line. It must not
    return raw rows for the model to sum: that is the constraint the whole design rests on.
    """

    role: AgentRole
    system_prompt: str
    task: str

    async def gather(self, tools: ScopedToolset) -> list[str]:
        """Call the allowlisted tools and render their results as brief lines."""
        ...

    def review(self, finding: AgentFinding) -> AgentFinding:
        """Deterministic post-check on the model's answer, before it is validated.

        Where policy has the last word it takes it here -- the AP agent that proposes
        deferring payroll has that proposal removed by code, not argued out of it by a
        prompt. Returning the finding unchanged is the normal case.
        """
        ...


class AgentRunner:
    """Runs one `AgentSpec` against one provider and one toolset."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        toolset: Toolset,
        routing: ModelRouting,
        bus: EventBus,
        company: str,
        as_of: str,
        investigation_id: str | None = None,
        incident: Incident | None = None,
    ) -> None:
        self._provider = provider
        self._toolset = toolset
        self._routing = routing
        self._bus = bus
        self._company = company
        self._as_of = as_of
        self.investigation_id = investigation_id
        self._incident = incident

    async def run(
        self,
        spec: AgentSpec,
        *,
        run_id: str,
        prior_findings: list[AgentFinding] | None = None,
        attempt: int = 1,
        queued_ms: int | None = None,
    ) -> AgentRun:
        route = self._routing.route(spec.role)
        budget = self._routing.budgets
        scoped = ScopedToolset(self._toolset, spec.role)
        started_at = datetime.now(UTC)
        began = time.perf_counter()

        self._bus.emit(
            AgentStarted,
            investigation_id=self.investigation_id,
            status_line=f"{spec.role.value}: working",
            agent=spec.role,
            run_id=run_id,
            model=route.model,
        )

        def record(
            status: AgentStatus,
            *,
            finding: AgentFinding | None = None,
            failure_reason: str | None = None,
            usage: TokenUsage | None = None,
            context_pack: str | None = None,
        ) -> AgentRun:
            latency_ms = int((time.perf_counter() - began) * 1000)
            run = AgentRun(
                run_id=run_id,
                investigation_id=self.investigation_id,
                agent=spec.role,
                status=status,
                model=route.model,
                started_at=started_at,
                ended_at=datetime.now(UTC),
                latency_ms=latency_ms,
                queued_ms=queued_ms,
                usage=usage or TokenUsage(),
                tool_calls=list(scoped.calls),
                context_pack=context_pack,
                finding=finding,
                failure_reason=failure_reason,
                attempts=attempt,
            )
            self._bus.emit(
                AgentStatusChanged,
                investigation_id=self.investigation_id,
                mark=STATUS_TO_MARK[status],
                status_line=f"{spec.role.value}: {status.value}",
                agent=spec.role,
                run_id=run_id,
                status=status,
                failure_reason=failure_reason[:400] if failure_reason else None,
                latency_ms=latency_ms,
                attempt=attempt,
            )
            return run

        # 1. Gather. A tool failure degrades this agent; the wave carries on without it.
        # `ToolNotAllowed` is deliberately not caught: an agent reaching outside its
        # allowlist is a wiring bug, and the executor records it as a failed run.
        try:
            brief_lines = await spec.gather(scoped)
        except ToolNotAllowed:
            raise
        except ToolError as exc:
            self._emit_tool_calls(spec.role, run_id, scoped)
            return record(AgentStatus.DEGRADED, failure_reason=f"tool layer: {exc}")
        self._emit_tool_calls(spec.role, run_id, scoped)

        # 2. Brief. Tool output is trimmed to its own budget before the pack is built.
        prompt = self._prompt(spec, brief_lines, prior_findings or [])
        request = LLMRequest(
            agent=spec.role,
            model=route.model,
            system=spec.system_prompt,
            messages=[Msg(role="user", content=prompt)],
            max_output_tokens=route.max_output_tokens,
            temperature=route.temperature,
        )

        # 3. Budget gate. Loud and early: an over-budget prompt is a defect we own.
        try:
            assert_within_budget(request, budget.system_prompt, budget.total_input)
        except BudgetExceeded as exc:
            return record(AgentStatus.FAILED, failure_reason=str(exc), context_pack=prompt)

        # 4. Call. LLMTimeout deliberately propagates: retries are the executor's job.
        try:
            finding, usage = await self._provider.complete(
                request, AgentFinding, timeout_s=route.timeout_s
            )
        except LLMTimeout:
            raise
        except LLMError as exc:
            return record(AgentStatus.FAILED, failure_reason=str(exc), context_pack=prompt)

        if finding.agent is not spec.role:
            return record(
                AgentStatus.FAILED,
                failure_reason=(
                    f"{spec.role.value} returned a finding attributed to {finding.agent.value}"
                ),
                usage=usage,
                context_pack=prompt,
            )

        # 5. Review. Policy has the last word, and it takes it before validation so that
        # any evidence a refusal adds goes through the same three gates as the model's.
        try:
            finding = spec.review(finding)
        except Exception as exc:
            return record(
                AgentStatus.FAILED,
                failure_reason=f"review gate: {type(exc).__name__}: {exc}",
                usage=usage,
                context_pack=prompt,
            )

        # 6. Validate. A citation the ledger cannot confirm never reaches a screen.
        verdict = await EvidenceValidator(self._toolset, scoped.references).validate(finding)
        for rejection in verdict.rejections:
            self._bus.emit(
                EvidenceRejected,
                investigation_id=self.investigation_id,
                status_line=f"{spec.role.value}: evidence rejected",
                agent=spec.role,
                run_id=run_id,
                reference=rejection.reference,
                reason=rejection.reason,
            )
        if not verdict.accepted:
            return record(
                AgentStatus.DEGRADED,
                failure_reason=f"evidence rejected -- {verdict.reason()}",
                usage=usage,
                context_pack=prompt,
            )

        verified = apply_verdict(finding, verdict)
        self._bus.emit(
            AgentFindingEmitted,
            investigation_id=self.investigation_id,
            mark=StatusMark.OK,
            status_line=f"{spec.role.value}: {verified.headline}"[:200],
            agent=spec.role,
            run_id=run_id,
            finding=verified,
        )

        # 7. Record. A refusal is an outcome, not an error: the AP agent asked to defer
        # payroll returns a constraint violation, and that run is complete-and-refused.
        status = verified.status if verified.status in AGENT_DECIDED else AgentStatus.COMPLETE
        reason = verified.detail[:400] if status is not AgentStatus.COMPLETE else None
        return record(
            status,
            finding=verified,
            usage=usage,
            context_pack=prompt,
            failure_reason=reason,
        )

    def _prompt(
        self, spec: AgentSpec, brief_lines: list[str], prior_findings: list[AgentFinding]
    ) -> str:
        budget = self._routing.budgets
        pack = assemble(
            agent=spec.role,
            company=self._company,
            as_of=self._as_of,
            task=spec.task,
            tools=sorted(tools_for(spec.role)),
            output_schema=FINDING_SCHEMA_NAME,
            budget=budget,
            incident=self._incident,
            prior_findings=prior_findings,
        )
        lines = fit_to_budget(brief_lines, budget.tool_results)
        rendered = "\n".join(f"- {line}" for line in lines)
        return f"{pack.render()}\n\nWHAT YOUR TOOLS RETURNED\n{rendered}"

    def _emit_tool_calls(self, role: AgentRole, run_id: str, scoped: ScopedToolset) -> None:
        """Tool calls are shown; their arguments and any reasoning are not."""
        for call in scoped.calls:
            self._bus.emit(
                AgentToolCall,
                investigation_id=self.investigation_id,
                mark=StatusMark.FAIL if call.error else StatusMark.WORKING,
                status_line=f"{role.value}: {call.tool}",
                agent=role,
                run_id=run_id,
                tool=call.tool,
                duration_ms=max(call.duration_ms, 0),
                row_count=call.row_count,
                truncated_from=call.truncated_from,
                error=call.error[:280] if call.error else None,
            )


def fit_to_budget(lines: list[str], budget_tokens: int) -> list[str]:
    """Keep brief lines in priority order until the tool-results budget is spent.

    Dropping lines is reported in the prompt itself, for the same reason tool truncation
    is: a cap the agent cannot see is a lie by omission.
    """
    kept: list[str] = []
    used = 0
    for index, line in enumerate(lines):
        cost = estimate_tokens(line)
        if used + cost > budget_tokens:
            kept.append(f"({len(lines) - index} further lines omitted for budget)")
            break
        kept.append(line)
        used += cost
    return kept


def assert_within_budget(request: LLMRequest, system_budget: int, input_budget: int) -> None:
    """LLM_STRATEGY section 4, enforced at runtime and asserted in CI."""
    system = estimate_tokens(request.system)
    total = system + sum(estimate_tokens(message.content) for message in request.messages)
    if system > system_budget:
        raise BudgetExceeded(
            f"{request.agent.value} system prompt is {system} tokens, "
            f"over the {system_budget} budget"
        )
    if total > input_budget:
        raise BudgetExceeded(
            f"{request.agent.value} call is {total} input tokens, over the {input_budget} budget"
        )
