"""One live Gemini structured-output call. Requires GEMINI_API_KEY in the environment."""

from __future__ import annotations

import asyncio
import os
import sys

from backend.agents.factory import build_provider, load_env
from backend.agents.provider import LLMRequest, Msg
from backend.contracts.agent import AgentFinding, AgentRole


async def main() -> int:
    load_env()
    os.environ.setdefault("WARROOM_LLM", "gemini")
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        print("GEMINI_API_KEY is not set", file=sys.stderr)
        return 1

    provider = build_provider(mode="gemini")
    request = LLMRequest(
        agent=AgentRole.VARIANCE,
        model="gemini-3.8-flash",
        system="Return only a schema-valid AgentFinding JSON object. No prose.",
        messages=[
            Msg(
                role="user",
                content=(
                    "Produce: agent=variance, status=complete, "
                    "headline='W09 receipts miss from major customer', "
                    "detail='Apex delayed a material receipt', "
                    "evidence=[{reference:'ar_ledger:inv_apex_1', source:'ar_ledger', "
                    "excerpt:'Invoice inv_apex_1 still open'}], "
                    "confidence={basis:'qualitative', band:'high', rationale:'smoke test'}, "
                    "risks=[], recommended_actions=[], rejects=[], requires_followup=false"
                ),
            )
        ],
        max_output_tokens=500,
    )
    finding, usage = await provider.complete(request, AgentFinding, timeout_s=60)
    print(f"ok provider={provider.name} model={request.model}")
    print(f"headline={finding.headline!r} status={finding.status.value} tokens={usage.total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
