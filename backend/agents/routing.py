"""Per-role model routing, token budgets and executor limits, loaded from config.

Model choice is configuration, not architecture: upgrading the Commander to a stronger
model is one line of `config/models.yaml` and no code at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.contracts.agent import AgentRole

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "models.yaml"


class TokenBudget(BaseModel):
    """LLM_STRATEGY.md section 4. Asserted in CI, logged per run."""

    model_config = ConfigDict(frozen=True)

    system_prompt: Annotated[int, Field(gt=0)]
    context_pack: Annotated[int, Field(gt=0)]
    findings_digest: Annotated[int, Field(gt=0)]
    tool_results: Annotated[int, Field(gt=0)]
    total_input: Annotated[int, Field(gt=0)]
    output: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def _parts_fit_the_whole(self) -> Self:
        parts = self.system_prompt + self.context_pack + self.findings_digest + self.tool_results
        if parts > self.total_input:
            raise ValueError(
                f"component budgets total {parts} tokens, above the {self.total_input} input cap"
            )
        return self


class ExecutorLimits(BaseModel):
    """Section 5: the wave is logically parallel even when it is physically throttled."""

    model_config = ConfigDict(frozen=True)

    max_in_flight: Annotated[int, Field(ge=1)] = 4
    requests_per_minute: Annotated[int, Field(ge=1)] = 15
    max_retries: Annotated[int, Field(ge=0)] = 3
    backoff_base_s: Annotated[float, Field(gt=0)] = 1.0
    backoff_jitter_s: Annotated[float, Field(ge=0)] = 0.5


class RoleRoute(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    temperature: float = 0.0
    timeout_s: Annotated[float, Field(gt=0)] = 30.0
    max_attempts: Annotated[int, Field(ge=1)] = 2
    max_output_tokens: Annotated[int, Field(gt=0)] = 500


class ModelRouting(BaseModel):
    """The whole routing table, with every role resolved against the defaults."""

    model_config = ConfigDict(frozen=True)

    version: int
    budgets: TokenBudget
    executor: ExecutorLimits
    routes: dict[AgentRole, RoleRoute]

    @model_validator(mode="after")
    def _every_role_is_routed(self) -> Self:
        missing = sorted(role.value for role in AgentRole if role not in self.routes)
        if missing:
            raise ValueError(f"no model routed for: {', '.join(missing)}")
        for role, route in self.routes.items():
            if route.max_output_tokens > self.budgets.output and role not in _OUTPUT_EXEMPT:
                raise ValueError(
                    f"{role.value} allows {route.max_output_tokens} output tokens, "
                    f"above the {self.budgets.output} budget"
                )
        return self

    def route(self, role: AgentRole) -> RoleRoute:
        return self.routes[role]


# The Commander emits a plan with a justification per skipped agent, so it carries a
# larger output allowance than a specialist finding. Widen this list only with a reason.
_OUTPUT_EXEMPT = frozenset({AgentRole.COMMANDER})


def load_routing(path: Path | None = None) -> ModelRouting:
    raw = yaml.safe_load((path or CONFIG_PATH).read_text(encoding="utf-8"))
    defaults = raw.get("defaults", {})

    routes: dict[AgentRole, RoleRoute] = {}
    for name, override in (raw.get("roles") or {}).items():
        routes[AgentRole(name)] = RoleRoute.model_validate({**defaults, **(override or {})})

    return ModelRouting(
        version=raw["version"],
        budgets=TokenBudget.model_validate(raw["budgets"]),
        executor=ExecutorLimits.model_validate(raw.get("executor") or {}),
        routes=routes,
    )
