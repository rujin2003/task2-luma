"""`GeminiProvider` -- Google Generative Language API behind the LLMProvider protocol.

Structured output is mandatory: every call sends a Gemini `responseSchema` derived from
the Pydantic model, and the response is validated against that same model before it
leaves this module. A schema-invalid reply is a `SchemaViolation`, never silent prose.
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from backend.agents.provider import LLMError, LLMRequest, LLMTimeout, SchemaViolation
from backend.agents.schema_repair import repair_finding_payload
from backend.contracts.agent import AgentFinding, TokenUsage

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_TYPE_MAP = {
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "object": "OBJECT",
    "array": "ARRAY",
}


def gemini_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """Convert a Pydantic JSON schema into Gemini's OpenAPI-ish responseSchema subset."""
    raw = schema.model_json_schema()
    defs = raw.get("$defs", {})
    cleaned = {key: value for key, value in raw.items() if key not in {"$defs", "$schema"}}
    return _simplify(cleaned, defs)


def _simplify(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_simplify(item, defs) for item in node]
    if not isinstance(node, dict):
        return node

    if "$ref" in node:
        ref_name = str(node["$ref"]).rsplit("/", 1)[-1]
        return _simplify(copy.deepcopy(defs[ref_name]), defs)

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in {
            "title",
            "description",
            "examples",
            "default",
            "minLength",
            "maxLength",
            "minimum",
            "maximum",
            "exclusiveMinimum",
            "exclusiveMaximum",
            "pattern",
            "additionalProperties",
            "$defs",
            "$schema",
        }:
            continue

        if key == "anyOf":
            variants = list(value)
            non_null = [
                _simplify(item, defs)
                for item in variants
                if not (isinstance(item, dict) and item.get("type") == "null")
            ]
            has_null = any(
                isinstance(item, dict) and item.get("type") == "null" for item in variants
            )
            if len(non_null) == 1 and isinstance(non_null[0], dict):
                out.update(non_null[0])
            else:
                types = {
                    str(item.get("type")).lower()
                    for item in non_null
                    if isinstance(item, dict) and "type" in item
                }
                # Decimal / Money fields often serialize as number|string; prefer STRING.
                if types <= {"number", "string"} and "string" in types:
                    out["type"] = "STRING"
                elif non_null and isinstance(non_null[0], dict):
                    out.update(non_null[0])
            if has_null:
                out["nullable"] = True
            continue

        if key == "type" and isinstance(value, str):
            out[key] = _TYPE_MAP.get(value, value.upper())
            continue

        out[key] = _simplify(value, defs)

    return out


class GeminiProvider:
    """Live Gemini calls. Failures surface as LLMError subclasses; never swallowed."""

    name = "gemini"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.AsyncClient | None = None,
        thinking_budget: int = 0,
    ) -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for GeminiProvider")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.thinking_budget = thinking_budget
        self._client = client
        self._owns_client = client is None

    @classmethod
    def from_env(cls) -> GeminiProvider:
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise LLMError("GEMINI_API_KEY is not set")
        base = os.environ.get("GEMINI_API_BASE", DEFAULT_BASE_URL)
        return cls(key, base_url=base)

    async def _client_ref(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def complete[OutputT: BaseModel](
        self,
        request: LLMRequest,
        schema: type[OutputT],
        *,
        timeout_s: float,
    ) -> tuple[OutputT, TokenUsage]:
        model = request.model.removeprefix("models/")
        url = f"{self.base_url}/models/{model}:generateContent"
        body = {
            "system_instruction": {"parts": [{"text": request.system}]},
            "contents": [
                {
                    "role": "user" if message.role == "user" else "model",
                    "parts": [{"text": message.content}],
                }
                for message in request.messages
            ],
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": max(request.max_output_tokens, 256),
                "responseMimeType": "application/json",
                "responseSchema": gemini_schema(schema),
                "thinkingConfig": {"thinkingBudget": self.thinking_budget},
            },
        }

        client = await self._client_ref()
        try:
            response = await client.post(
                url,
                params={"key": self.api_key},
                json=body,
                timeout=timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeout(
                f"{request.agent.value} exceeded {timeout_s}s calling {model}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Gemini transport error for {request.agent.value}: {exc}") from exc

        if response.status_code == 429:
            raise LLMError(f"Gemini rate-limited {request.agent.value} ({model})")
        if response.status_code >= 400:
            detail = _error_message(response)
            raise LLMError(
                f"Gemini HTTP {response.status_code} for {request.agent.value}: {detail}"
            )

        payload = response.json()
        text = _extract_text(payload)
        if not text:
            raise SchemaViolation(
                f"{request.agent.value} returned no JSON text (finish={_finish_reason(payload)!r})"
            )

        try:
            if schema is AgentFinding:
                data = repair_finding_payload(json.loads(text))
                output = schema.model_validate(data)
            else:
                output = schema.model_validate_json(text)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            # A truncated reply fails as "invalid JSON at line N", which sends whoever
            # reads it hunting for a schema bug that is not there. The finish reason is
            # the actual diagnosis, so say it: the cap is too small for the contract.
            if _finish_reason(payload) == "MAX_TOKENS":
                raise SchemaViolation(
                    f"{request.agent.value} was cut off at its "
                    f"{request.max_output_tokens}-token output cap before it finished "
                    f"the {schema.__name__} JSON; raise max_output_tokens for this role"
                ) from exc
            raise SchemaViolation(
                f"{request.agent.value} returned JSON that failed {schema.__name__}: {exc}"
            ) from exc

        usage = _usage(payload)
        return output, usage


def _error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
        err = data.get("error") or {}
        return str(err.get("message") or data)[:400]
    except Exception:
        return response.text[:400]


def _finish_reason(payload: dict[str, Any]) -> str | None:
    candidates = payload.get("candidates") or []
    if not candidates:
        return None
    return candidates[0].get("finishReason")


def _extract_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        return ""
    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    chunks: list[str] = []
    for part in parts:
        if part.get("thought"):
            continue
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            chunks.append(text)
    return "".join(chunks).strip()


def _usage(payload: dict[str, Any]) -> TokenUsage:
    meta = payload.get("usageMetadata") or {}
    return TokenUsage(
        input_tokens=int(meta.get("promptTokenCount") or 0),
        output_tokens=int(meta.get("candidatesTokenCount") or 0),
    )
