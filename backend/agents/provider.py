"""The provider-agnostic LLM interface.

Two rules make this interface what it is:

* **Structured output is mandatory, never optional.** Every call names a Pydantic schema
  and gets an instance back. There is no free-text path, which is what keeps the Agent
  Output Contract enforceable and the output token count down.
* **The provider knows nothing about treasury.** It takes a system prompt, messages and a
  schema. Context awareness lives in the tool layer and the Context Pack, never here.

`GeminiProvider` is the runtime target; `ReplayProvider` replays recordings and is what
makes the golden-path demo deterministic and the test suite free.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from backend.contracts.agent import AgentRole, TokenUsage


class LLMError(RuntimeError):
    """Any provider failure. Recorded on the run; surfaced as degraded, never swallowed."""


class LLMTimeout(LLMError):
    """The call exceeded its per-agent timeout. Distinct from a failure: it may be retried."""


class SchemaViolation(LLMError):
    """The model returned something that is not the requested schema, after retries."""


class RecordingMissing(LLMError):
    """`ReplayProvider` was asked for a call it has no recording of."""


class Msg(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Literal["user", "model"]
    content: str


class LLMRequest(BaseModel):
    """Everything one call needs. Frozen so the fingerprint below is stable."""

    model_config = ConfigDict(frozen=True)

    agent: AgentRole
    model: str
    system: str
    messages: list[Msg] = Field(default_factory=list)
    max_output_tokens: int = 500
    temperature: float = 0.0

    def fingerprint(self) -> str:
        """Stable id for this exact call. The replay key, and the cache key later.

        Deliberately excludes the model id: swapping the model for a role should not
        invalidate every recording, and the recordings live per role anyway.
        """
        payload = json.dumps(
            {
                "agent": self.agent.value,
                "system": self.system,
                "messages": [message.model_dump() for message in self.messages],
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@runtime_checkable
class LLMProvider(Protocol):
    """One method. Providers are interchangeable; routing is configuration."""

    name: str

    async def complete[OutputT: BaseModel](
        self,
        request: LLMRequest,
        schema: type[OutputT],
        *,
        timeout_s: float,
    ) -> tuple[OutputT, TokenUsage]:
        """Return a schema-valid instance and the tokens it cost."""
        ...


def estimate_tokens(text: str) -> int:
    """Rough token estimate for budget assertions.

    Deliberately provider-independent and slightly pessimistic: the budget in
    `LLM_STRATEGY.md` section 4 is a design constraint we want to fail early on, not an
    exact billing figure. Replace with the provider's counter when one is wired up.
    """
    return (len(text) + 3) // 4
