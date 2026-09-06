"""`ReplayProvider` -- replays recorded completions instead of calling a model.

This is the most load-bearing piece of test infrastructure in the project. It makes the
golden-path demo byte-identical on every run, makes the test suite free, and lets the six
agents be built before an API key exists. Failure injection lives here too: an agent that
times out or returns malformed output has to be exercised deliberately, not hoped for.

A recording is keyed by `LLMRequest.fingerprint()`. When a call has no recording the
provider raises with the fingerprint it computed, so adding the missing case is mechanical.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.agents.provider import (
    LLMRequest,
    LLMTimeout,
    RecordingMissing,
    SchemaViolation,
    estimate_tokens,
)
from backend.contracts.agent import AgentRole, TokenUsage

RECORDING_ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "llm"


class Recording(BaseModel):
    """One replayed completion, or one deliberately injected failure."""

    model_config = ConfigDict(frozen=True)

    agent: AgentRole
    fingerprint: str | None = None
    note: str = ""
    output: dict[str, Any] | None = None
    usage: TokenUsage | None = None

    # Failure injection. A runtime that has never seen these is a runtime that will meet
    # them for the first time during the demo.
    raises: Annotated[str, Field(pattern="^(timeout|schema_violation)$")] | None = None

    # Used when no fingerprint matches. Exactly one default per role, at most.
    default: bool = False

    @model_validator(mode="after")
    def _returns_something(self) -> Self:
        if self.output is None and self.raises is None:
            raise ValueError("a recording must carry an output or a failure to raise")
        if self.fingerprint is None and not self.default:
            raise ValueError("a recording needs a fingerprint unless it is the role default")
        return self


class ReplayProvider:
    """Replays `tests/fixtures/llm/<role>.json`. Deterministic by construction."""

    name = "replay"

    def __init__(self, root: Path | None = None, *, strict: bool = False) -> None:
        self.root = root or RECORDING_ROOT
        # strict=True refuses to fall back to a role default -- what the golden-path test
        # uses, so an unrecorded call fails loudly instead of quietly replaying something
        # plausible.
        self.strict = strict
        self.requests: list[LLMRequest] = []
        self._cache: dict[AgentRole, list[Recording]] = {}

    def _recordings(self, agent: AgentRole) -> list[Recording]:
        if agent not in self._cache:
            path = self.root / f"{agent.value}.json"
            if not path.exists():
                self._cache[agent] = []
            else:
                raw = json.loads(path.read_text(encoding="utf-8"))
                self._cache[agent] = [Recording.model_validate(item) for item in raw]
        return self._cache[agent]

    def _match(self, request: LLMRequest) -> Recording:
        fingerprint = request.fingerprint()
        recordings = self._recordings(request.agent)

        for recording in recordings:
            if recording.fingerprint == fingerprint:
                return recording

        if not self.strict:
            for recording in recordings:
                if recording.default:
                    return recording

        raise RecordingMissing(
            f"no recording for {request.agent.value} fingerprint {fingerprint}; "
            f"add one to {self.root / f'{request.agent.value}.json'}"
        )

    async def complete[OutputT: BaseModel](
        self,
        request: LLMRequest,
        schema: type[OutputT],
        *,
        timeout_s: float,
    ) -> tuple[OutputT, TokenUsage]:
        self.requests.append(request)
        recording = self._match(request)

        if recording.raises == "timeout":
            raise LLMTimeout(f"{request.agent.value} exceeded {timeout_s}s (injected)")
        if recording.raises == "schema_violation":
            raise SchemaViolation(f"{request.agent.value} returned malformed output (injected)")

        assert recording.output is not None
        try:
            output = schema.model_validate(recording.output)
        except ValidationError as exc:
            raise SchemaViolation(
                f"recording for {request.agent.value} does not match {schema.__name__}: {exc}"
            ) from exc

        usage = recording.usage or TokenUsage(
            input_tokens=estimate_tokens(request.system)
            + sum(estimate_tokens(message.content) for message in request.messages),
            output_tokens=estimate_tokens(json.dumps(recording.output)),
        )
        return output, usage
