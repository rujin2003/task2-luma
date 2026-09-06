"""Model routing, token budgets, and the replay provider the whole demo rests on."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agents.fake import FakeProvider, Recording
from backend.agents.provider import (
    LLMProvider,
    LLMRequest,
    LLMTimeout,
    Msg,
    RecordingMissing,
    SchemaViolation,
    estimate_tokens,
)
from backend.agents.routing import CONFIG_PATH, TokenBudget, load_routing
from backend.contracts import AgentFinding, AgentRole, AgentStatus


@pytest.fixture(scope="module")
def routing():
    return load_routing()


def _request(agent: AgentRole = AgentRole.VARIANCE, **overrides) -> LLMRequest:
    return LLMRequest.model_validate(
        {
            "agent": agent,
            "model": "gemini-3.7-flash",
            "system": "You are the Variance agent.",
            "messages": [Msg(role="user", content="Explain the W09 receipts miss.")],
            **overrides,
        }
    )


# --- routing -------------------------------------------------------------------------


def test_the_shipped_config_routes_every_role(routing) -> None:
    assert CONFIG_PATH.exists()
    for role in AgentRole:
        route = routing.route(role)
        assert route.model
        assert route.temperature == 0.0, f"{role.value} must be deterministic"


def test_the_two_risky_roles_are_routed_to_a_stronger_model(routing) -> None:
    """LLM_STRATEGY section 6: planning and conflict resolution are the risk rows."""
    flash = routing.route(AgentRole.AR_COLLECTIONS).model
    assert routing.route(AgentRole.COMMANDER).model != flash
    assert routing.route(AgentRole.CONFLICT_RESOLUTION).model != flash


def test_component_budgets_must_fit_inside_the_input_cap() -> None:
    with pytest.raises(ValueError, match="above the"):
        TokenBudget(
            system_prompt=2000,
            context_pack=2000,
            findings_digest=400,
            tool_results=1500,
            total_input=4000,
            output=500,
        )


def test_the_shipped_budget_matches_the_strategy_document(routing) -> None:
    budget = routing.budgets
    assert budget.total_input <= 4000
    assert budget.output <= 500


def test_a_role_cannot_quietly_exceed_the_output_budget(tmp_path: Path, routing) -> None:
    """A prompt change that doubles the output allowance fails the build, not the demo."""
    raw = CONFIG_PATH.read_text(encoding="utf-8").replace(
        "  ar_collections: {}", "  ar_collections:\n    max_output_tokens: 4000"
    )
    path = tmp_path / "models.yaml"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(ValueError, match="above the 500 budget"):
        load_routing(path)


def test_executor_limits_are_bounded(routing) -> None:
    """A free tier throttles a nine-agent wave; unbounded gather is not an option."""
    assert routing.executor.max_in_flight < len(AgentRole)
    assert routing.executor.requests_per_minute >= 1


# --- request fingerprinting ----------------------------------------------------------


def test_the_fingerprint_is_stable_and_message_sensitive() -> None:
    assert _request().fingerprint() == _request().fingerprint()
    other = _request(messages=[Msg(role="user", content="Explain the W10 receipts miss.")])
    assert other.fingerprint() != _request().fingerprint()


def test_the_fingerprint_ignores_the_model_id() -> None:
    """Upgrading a role's model must not invalidate every recording."""
    assert _request(model="gemini-3.7-pro").fingerprint() == _request().fingerprint()


# --- the replay provider -------------------------------------------------------------


def test_the_fake_provider_satisfies_the_interface() -> None:
    assert isinstance(FakeProvider(), LLMProvider)


async def test_a_recorded_finding_replays_schema_valid() -> None:
    provider = FakeProvider()
    finding, usage = await provider.complete(_request(), AgentFinding, timeout_s=30)

    assert isinstance(finding, AgentFinding)
    assert finding.agent is AgentRole.VARIANCE
    assert finding.status is AgentStatus.COMPLETE
    assert finding.evidence, "a complete finding must cite evidence"
    assert usage.total > 0


async def test_replay_is_byte_identical_across_runs() -> None:
    first, _ = await FakeProvider().complete(_request(), AgentFinding, timeout_s=30)
    second, _ = await FakeProvider().complete(_request(), AgentFinding, timeout_s=30)
    assert first.model_dump_json() == second.model_dump_json()


async def test_an_injected_timeout_is_raised_as_a_timeout() -> None:
    """The wave has to meet this in a test, not for the first time in the demo."""
    provider = FakeProvider()
    with pytest.raises(LLMTimeout):
        await provider.complete(_request(AgentRole.DODO_REVENUE), AgentFinding, timeout_s=30)


async def test_strict_mode_refuses_to_improvise(tmp_path: Path) -> None:
    provider = FakeProvider(strict=True)
    with pytest.raises(RecordingMissing) as caught:
        await provider.complete(_request(), AgentFinding, timeout_s=30)
    # The message carries the fingerprint, so adding the recording is mechanical.
    assert _request().fingerprint() in str(caught.value)


async def test_an_unrecorded_role_is_missing_not_empty() -> None:
    """The Cartographer has no recordings yet, and asking for one must say so."""
    provider = FakeProvider()
    with pytest.raises(RecordingMissing):
        await provider.complete(_request(AgentRole.CARTOGRAPHER), AgentFinding, timeout_s=30)


async def test_a_recording_that_no_longer_matches_the_schema_fails_loudly(
    tmp_path: Path,
) -> None:
    """A contract change must break the recordings, not silently pass a bad shape on."""
    (tmp_path / "variance.json").write_text(
        json.dumps(
            [
                {
                    "agent": "variance",
                    "default": True,
                    "output": {"agent": "variance", "status": "complete"},
                }
            ]
        ),
        encoding="utf-8",
    )
    provider = FakeProvider(tmp_path)
    with pytest.raises(SchemaViolation):
        await provider.complete(_request(), AgentFinding, timeout_s=30)


def test_a_recording_must_return_something() -> None:
    with pytest.raises(ValueError, match="output or a failure"):
        Recording(agent=AgentRole.VARIANCE, default=True)


def test_a_recording_needs_a_key() -> None:
    with pytest.raises(ValueError, match="fingerprint"):
        Recording(agent=AgentRole.VARIANCE, output={})


def test_token_estimates_are_pessimistic_but_sane() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 400) == 100
