"""What every specialist shares: a prompt, a task, a gather, and a deterministic review.

A specialist is deliberately thin. All six are the same runtime with a different prompt
and a different set of tool calls, because the moment one of them grows its own control
flow is the moment its failures stop looking like the others'.

`gather` renders tool output as **brief lines**: one decision per line, already computed,
each carrying the reference the agent is allowed to cite. The model never sees a table.

`review` is the deterministic gate after the model answers. Policy has the last word --
the AP agent that proposes deferring payroll has that proposal taken off it by code, not
talked out of it by a prompt.
"""

from __future__ import annotations

from typing import ClassVar

from backend.agents.prompts import load_prompt
from backend.contracts.agent import AgentFinding, AgentRole
from backend.contracts.money import Money
from backend.contracts.provenance import Evidence, SourceSystem
from backend.tools.registry import ScopedToolset
from backend.tools.results import ToolPayload
from backend.tools.toolset import ToolError


class Specialist:
    """Base for the six. Subclasses set `role`, `default_task` and implement `gather`."""

    role: ClassVar[AgentRole]
    default_task: ClassVar[str]

    def __init__(self, *, task: str | None = None) -> None:
        self.system_prompt = load_prompt(self.role)
        self.task = task or self.default_task

    async def gather(self, tools: ScopedToolset) -> list[str]:
        raise NotImplementedError

    def review(self, finding: AgentFinding) -> AgentFinding:
        """Deterministic post-check. The default specialist has nothing to add."""
        return finding


async def fetch[PayloadT: ToolPayload](
    tools: ScopedToolset, tool: str, payload_type: type[PayloadT], /, **arguments: object
) -> PayloadT:
    """Call a tool and assert the shape it returned.

    The Protocol already promises this, but the engine lands behind those signatures later
    and a payload that drifted should fail here -- inside the run record -- rather than as
    an attribute error halfway through rendering a brief.
    """
    payload = await tools.call(tool, **arguments)
    if not isinstance(payload, payload_type):
        raise ToolError(
            f"{tool} returned {type(payload).__name__}, expected {payload_type.__name__}"
        )
    return payload


def cite(line: str, reference: str) -> str:
    """Every brief line carries the citation it licenses, and no other."""
    return f"{line} [{reference}]"


def money(amount: Money | None) -> str:
    return str(amount) if amount is not None else "n/a"


def evidence_for(reference: str, excerpt: str) -> Evidence:
    """Build a citation from a reference the tools returned.

    The excerpt is provisional: the evidence validator replaces it with the source row's
    own text before anything is surfaced.
    """
    prefix = reference.split(":", 1)[0]
    source = SourceSystem(prefix) if prefix in set(SourceSystem) else SourceSystem.GL
    return Evidence(reference=reference, source=source, excerpt=excerpt[:280])
