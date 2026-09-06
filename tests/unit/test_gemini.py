"""GeminiProvider: schema conversion and HTTP structured-output path."""

from __future__ import annotations

import json

import httpx
import pytest

from backend.agents.factory import build_provider
from backend.agents.gemini import GeminiProvider, gemini_schema
from backend.agents.provider import LLMError, LLMRequest, Msg, SchemaViolation
from backend.agents.replay import ReplayProvider
from backend.contracts.agent import AgentFinding, AgentRole


def _request(**overrides) -> LLMRequest:
    return LLMRequest.model_validate(
        {
            "agent": AgentRole.VARIANCE,
            "model": "gemini-3.8-flash",
            "system": "Return a schema-valid AgentFinding.",
            "messages": [Msg(role="user", content="headline for W09 receipts miss")],
            "max_output_tokens": 500,
            **overrides,
        }
    )


def test_gemini_schema_inlines_refs_and_uses_gemini_types() -> None:
    schema = gemini_schema(AgentFinding)
    assert schema["type"] == "OBJECT"
    assert "additionalProperties" not in schema
    assert "$ref" not in json.dumps(schema)
    assert schema["properties"]["agent"]["type"] == "STRING"
    assert "variance" in schema["properties"]["agent"]["enum"]
    # Money.minor_units must survive as INTEGER, not a Decimal anyOf mess.
    quantum = schema["properties"]["quantum"]
    assert quantum["nullable"] is True
    assert quantum["properties"]["minor_units"]["type"] == "INTEGER"


def test_build_provider_honours_fake_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WARROOM_LLM", "replay")
    monkeypatch.setenv("GEMINI_API_KEY", "should-be-ignored")
    assert isinstance(build_provider(), ReplayProvider)


def test_build_provider_requires_key_for_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WARROOM_LLM", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        build_provider()


async def test_gemini_provider_validates_structured_json() -> None:
    finding = {
        "agent": "variance",
        "status": "complete",
        "headline": "W09 receipts miss",
        "detail": "Apex delayed payment",
        "evidence": [
            {
                "reference": "ar_ledger:inv_1",
                "source": "ar_ledger",
                "excerpt": "Invoice inv_1 open",
            }
        ],
        "confidence": {"basis": "qualitative", "band": "high", "rationale": "fixture"},
        "risks": [],
        "recommended_actions": [],
        "rejects": [],
        "requires_followup": False,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "gemini-3.8-flash:generateContent" in str(request.url)
        body = json.loads(request.content)
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert body["generationConfig"]["responseSchema"]["type"] == "OBJECT"
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": json.dumps(finding)}]}}],
                "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 40},
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GeminiProvider("test-key", client=client)
        output, usage = await provider.complete(_request(), AgentFinding, timeout_s=30)

    assert output.headline == "W09 receipts miss"
    assert usage.input_tokens == 12
    assert usage.output_tokens == 40


async def test_a_truncated_reply_names_the_cap_rather_than_the_json() -> None:
    """A cut-off reply fails as "invalid JSON at line N", which sends the reader hunting
    for a schema bug that is not there. The finish reason is the actual diagnosis."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"parts": [{"text": '{"agent":"variance","headline":"W0'}]},
                        "finishReason": "MAX_TOKENS",
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GeminiProvider("test-key", client=client)
        with pytest.raises(SchemaViolation, match="output cap"):
            await provider.complete(_request(), AgentFinding, timeout_s=30)


async def test_gemini_provider_rejects_invalid_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": '{"agent":"variance"}'}]}}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GeminiProvider("test-key", client=client)
        with pytest.raises(SchemaViolation):
            await provider.complete(_request(), AgentFinding, timeout_s=30)


async def test_complete_without_evidence_is_coerced_to_degraded() -> None:
    """Gemini cannot encode 'complete requires evidence' in responseSchema, so it
    sometimes returns complete with an empty list. That is degraded, not a hard fail."""

    finding = {
        "agent": "ar_collections",
        "status": "complete",
        "headline": "$1.2M expected against open AR",
        "detail": "Ranked on the empirical curve; the open total is uncollectible.",
        "evidence": [],
        "confidence": {"basis": "qualitative", "band": "medium", "rationale": "tenant curve"},
        "risks": [],
        "recommended_actions": [],
        "rejects": [],
        "requires_followup": False,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": json.dumps(finding)}]}}],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20},
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GeminiProvider("test-key", client=client)
        output, _ = await provider.complete(
            _request(agent=AgentRole.AR_COLLECTIONS), AgentFinding, timeout_s=30
        )

    assert output.status.value == "degraded"
    assert "without citations" in output.detail
