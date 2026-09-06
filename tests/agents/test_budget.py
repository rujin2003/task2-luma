"""Token budgets, asserted in CI.

LLM_STRATEGY section 4 is only a design constraint if a prompt change that doubles the
context fails the build rather than the demo. Two guards: every shipped prompt file fits
the system-prompt budget, and the runtime gate refuses an over-budget call before it is
paid for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.agents.provider import LLMRequest, Msg, estimate_tokens
from backend.agents.routing import load_routing
from backend.agents.runner import BudgetExceeded, assert_within_budget, fit_to_budget
from backend.contracts import AgentRole

BUDGET = load_routing().budgets
PROMPT_ROOT = Path(__file__).resolve().parents[2] / "prompts"


def request(system: str = "You are the Variance agent.", user: str = "Explain W09.") -> LLMRequest:
    return LLMRequest(
        agent=AgentRole.VARIANCE,
        model="gemini-3.7-flash",
        system=system,
        messages=[Msg(role="user", content=user)],
    )


@pytest.mark.parametrize("path", sorted(PROMPT_ROOT.glob("*.md")), ids=lambda p: p.name)
def test_every_shipped_prompt_fits_the_system_prompt_budget(path: Path) -> None:
    tokens = estimate_tokens(path.read_text(encoding="utf-8"))
    assert tokens <= BUDGET.system_prompt, (
        f"{path.name} is {tokens} tokens, over the {BUDGET.system_prompt} budget"
    )


def test_the_gate_rejects_a_bloated_system_prompt() -> None:
    with pytest.raises(BudgetExceeded, match="system prompt"):
        assert_within_budget(
            request(system="cite. " * 2000), BUDGET.system_prompt, BUDGET.total_input
        )


def test_the_gate_rejects_a_call_that_is_over_the_input_cap() -> None:
    with pytest.raises(BudgetExceeded, match="input tokens"):
        assert_within_budget(
            request(user="row. " * 4000), BUDGET.system_prompt, BUDGET.total_input
        )


def test_a_call_inside_the_budget_passes_quietly() -> None:
    assert_within_budget(request(), BUDGET.system_prompt, BUDGET.total_input)


def test_tool_results_are_trimmed_and_the_trimming_is_reported() -> None:
    """A cap the agent cannot see is a lie by omission -- the same rule as tool truncation."""
    lines = [f"row {index}: a pre-aggregated decision line" for index in range(500)]

    kept = fit_to_budget(lines, BUDGET.tool_results)

    assert len(kept) < len(lines)
    assert "further lines omitted for budget" in kept[-1]
    assert estimate_tokens("\n".join(kept)) <= BUDGET.tool_results + 20


def test_short_tool_output_survives_intact() -> None:
    lines = ["rank_collection_opportunities: 8 rows, $2.6M weighted"]

    assert fit_to_budget(lines, BUDGET.tool_results) == lines
