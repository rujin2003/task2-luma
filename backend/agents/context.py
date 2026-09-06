"""The Context Pack -- a deterministic brief assembled by code before every agent call.

Context awareness lives here and in the tool layer, never in a large prompt. The pack is
~600 tokens: who the company is, what the policy says, what this tenant can actually see,
what happened, what the other agents have concluded so far, and what this agent is being
asked to do.

Two properties are load-bearing:

* **Deterministic.** The same inputs render the same string, so `ReplayProvider` recordings
  keyed on the request fingerprint stay valid.
* **Bounded.** The digest is trimmed to its budget rather than allowed to grow with the
  investigation. Cross-agent context grows linearly and slowly, not quadratically.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from backend.agents.provider import estimate_tokens
from backend.agents.routing import TokenBudget
from backend.contracts.agent import AgentFinding, AgentRole
from backend.tools.results import CapabilityManifest, LiquidityPosition, PolicyConstraints

#: The parts of the output contract a JSON schema cannot express.
#:
#: `AgentFinding` and `Confidence` both carry Pydantic `model_validator` rules — a
#: complete finding must cite evidence, a rejection must attach it, an empirical
#: confidence must carry measured error. Gemini's `responseSchema` subset has no way to
#: represent a conditional requirement, so a model shown only the schema will reasonably
#: return `status: "complete"` with an empty evidence list, or `basis: "empirical"` with
#: null error fields, and the run fails validation on a rule nobody told it.
#:
#: These are stated here rather than in six prompt files because they are one contract,
#: and a rule copied six times is a rule that will be five places out of date. Changing
#: this list moves every recording's fingerprint, which is the intended friction: it is a
#: change to what every agent was asked for.
CONTRACT_RULES = [
    "status complete requires at least one evidence reference; if you have nothing you can"
    " cite, return degraded or refused and say why in detail",
    "rejecting another agent's proposal requires attached evidence",
    "cite only references your tools actually returned; an unresolvable citation is rejected",
    "empirical confidence: give mape_pct and sample_size, and no band",
    "qualitative confidence: give band, and no mape_pct or sample_size",
    "claim empirical only where a tool returned measured error; otherwise qualitative",
]


class Incident(BaseModel):
    """The specific, dated, quantified condition the war room opened on."""

    model_config = ConfigDict(frozen=True)

    trigger: Annotated[str, Field(min_length=1, max_length=200)]
    detected_on: str
    detail: Annotated[str, Field(max_length=400)] = ""


class ContextPack(BaseModel):
    """Assembled by code, rendered as a compact brief. Never hand-written per agent."""

    model_config = ConfigDict(frozen=True)

    agent: AgentRole
    company: str
    as_of: str
    incident: Incident | None = None
    position_lines: list[str] = Field(default_factory=list)
    policy_lines: list[str] = Field(default_factory=list)
    capability_lines: list[str] = Field(default_factory=list)
    digest_lines: list[str] = Field(default_factory=list)
    task: Annotated[str, Field(min_length=1, max_length=400)]
    tools: list[str] = Field(default_factory=list)
    output_schema: str

    def render(self) -> str:
        sections: list[tuple[str, list[str]]] = [
            ("COMPANY", [f"{self.company}, as of {self.as_of}"]),
            (
                "INCIDENT",
                [f"{self.incident.trigger} (detected {self.incident.detected_on})"]
                + ([self.incident.detail] if self.incident and self.incident.detail else [])
                if self.incident
                else [],
            ),
            ("POSITION", self.position_lines),
            ("POLICY", self.policy_lines),
            ("CAPABILITIES", self.capability_lines),
            ("PRIOR FINDINGS", self.digest_lines),
            ("TASK", [self.task]),
            ("TOOLS", [", ".join(self.tools)] if self.tools else []),
            ("OUTPUT", [f"Return one {self.output_schema}. No prose, no reasoning."]),
            ("CONTRACT", CONTRACT_RULES),
        ]
        blocks = [
            f"{heading}\n" + "\n".join(f"- {line}" for line in lines)
            for heading, lines in sections
            if lines
        ]
        return "\n\n".join(blocks)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.render())


def assemble(
    *,
    agent: AgentRole,
    company: str,
    as_of: str,
    task: str,
    tools: list[str],
    output_schema: str,
    budget: TokenBudget,
    incident: Incident | None = None,
    position: LiquidityPosition | None = None,
    policy: PolicyConstraints | None = None,
    capabilities: CapabilityManifest | None = None,
    prior_findings: list[AgentFinding] | None = None,
) -> ContextPack:
    """Build the pack, trimming the digest until the whole thing fits its budget."""
    pack = ContextPack(
        agent=agent,
        company=company,
        as_of=as_of,
        incident=incident,
        position_lines=_position_lines(position),
        policy_lines=_policy_lines(policy),
        capability_lines=_capability_lines(capabilities),
        digest_lines=_digest_lines(prior_findings or [], budget.findings_digest),
        task=task,
        tools=sorted(tools),
        output_schema=output_schema,
    )

    # The digest is the only elastic part: everything else is the agent's situation and
    # dropping it would be lying by omission. Trim oldest-first until the pack fits.
    while pack.tokens > budget.context_pack and pack.digest_lines:
        pack = pack.model_copy(update={"digest_lines": pack.digest_lines[1:]})
    return pack


def _position_lines(position: LiquidityPosition | None) -> list[str]:
    if position is None:
        return []
    lines = [
        f"cash today {position.cash_today}",
        f"13-week minimum {position.min_cash} at W{position.min_cash_week}, floor {position.floor}",
        f"revolver available {position.revolver_available} "
        f"({position.revolver_utilization_pct}% utilised)",
    ]
    if position.breaches_floor:
        lines.append("the minimum breaches the policy floor")
    return lines


def _policy_lines(policy: PolicyConstraints | None) -> list[str]:
    if policy is None:
        return []
    return [
        f"{constraint.constraint_id} ({constraint.severity.value}): {constraint.description}"
        for constraint in policy.constraints
    ]


def _capability_lines(capabilities: CapabilityManifest | None) -> list[str]:
    if capabilities is None:
        return []
    lines = [f"available: {', '.join(s.value for s in capabilities.available_sources)}"]
    if capabilities.missing_sources:
        # An agent has to know what this tenant cannot see, or it will infer from silence.
        lines.append(f"not available: {', '.join(s.value for s in capabilities.missing_sources)}")
    return lines


def _digest_lines(findings: list[AgentFinding], budget_tokens: int) -> list[str]:
    """Conclusions only -- one line, the number, the status. Never another agent's output.

    Filled newest-first and then restored to chronological order, so a long investigation
    drops what an agent was told an hour ago rather than what just happened. The pack
    trims from the same end, which keeps the two layers agreeing.
    """
    lines: list[str] = []
    used = 0
    for finding in reversed(findings):
        line = finding.digest_line()
        cost = estimate_tokens(line)
        if used + cost > budget_tokens:
            break
        lines.append(line)
        used += cost
    return list(reversed(lines))
