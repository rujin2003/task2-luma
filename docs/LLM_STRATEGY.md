# WAR ROOM — LLM Strategy

**Decision:** target Google Gemini Flash as the default runtime model, behind a
provider-agnostic interface. The spec already requires this ("Keep agent interfaces clean
and provider-agnostic"), so model choice is configuration, not architecture.

---

## 1. Model selection — verify before committing

**"Gemini 3.1 Flash" appears to be superseded.** As of this writing Google's docs banner
reads *"Gemini 3.8 Flash is now available"* and their code samples use `gemini-3.7-flash`.
The API endpoint `https://generativelanguage.googleapis.com/v1beta/models` is live and
key-gated.

**Unverified — confirm before building against them:** exact model IDs, context window,
pricing, free-tier rate limits, structured-output support, and function-calling support.
The docs pages are client-rendered and could not be read programmatically during planning.

Do this first, and record the answers in this file:

```bash
curl "https://generativelanguage.googleapis.com/v1beta/models?key=$GEMINI_API_KEY" \
  | jq '.models[] | {name, inputTokenLimit, outputTokenLimit, supportedGenerationMethods}'
```

### Two things to check with care

**Free-tier data handling.** Free tiers commonly carry different retention and
training-use terms than paid tiers. WAR ROOM will process real customer ledgers, bank
balances, and covenant positions. Confirm the terms that apply to your tier before any
tenant data reaches the API — for a treasury product this is likely a hard procurement
blocker, and it is much cheaper to discover now than during a customer's security review.
The Schema Cartographer is already designed so that **no raw financial rows** are sent to
a model; extend that discipline to the runtime agents wherever possible.

**Free-tier rate limits.** The spec calls for a parallel agent wave. Nine agents firing
simultaneously will hit per-minute request limits on a free tier. Plan for it (§5).

---

## 2. Provider-agnostic interface

One interface, config-driven routing per agent role. Swapping providers, or upgrading one
role to a stronger model, is a YAML change.

```python
class LLMProvider(Protocol):
    async def complete(
        self,
        system: str,
        messages: list[Msg],
        schema: type[BaseModel],   # structured output is mandatory, never optional
        max_output_tokens: int,
        timeout_s: float,
    ) -> tuple[BaseModel, Usage]: ...
```

```yaml
# config/models.yaml
default: {provider: gemini, model: gemini-3.7-flash}
roles:
  commander:          {effort: high}
  conflict_resolution: {effort: high}
  covenant_explainer: {max_output_tokens: 400}
  cartographer:       {max_output_tokens: 300}
```

Two implementations from day one — `GeminiProvider` and a `FakeProvider` that replays
recorded fixtures. The fake is what makes the golden-path demo deterministic and the test
suite free.

---

## 3. "Context aware about everything" — resolving the tension

A small, fast model cannot hold a company's ledger in context, and it should not need to.
**The model is never made aware of everything. Code is.**

Context awareness comes from three places, none of which is a large prompt:

**a. The tool layer (Phase 5).** Agents call the deterministic engine. `get_cash_position()`
returns a computed answer, not a table to reason over. The engine is omniscient; the model
is not, and does not need to be.

**b. The Context Pack.** A ~2 KB deterministic brief assembled by code before every agent
call:

```
Company: <name> | Currency: USD | Capabilities: bank,ar,ap,dodo (no fx, no debt)
Policy: min_cash 15.0M | min_30d_liquidity 20.0M | max_revolver_util 70%
Incident: liquidity_warning | 30d projected min 18.2M vs threshold 20.0M
Prior findings: cash_position ✓ 23.8M unrestricted | dodo ✓ 900K at risk
Your task: <one sentence> | Your tools: <3-5 tools> | Return: <schema name>
```

Everything an agent needs to be situated, in a few hundred tokens.

**c. The findings digest.** Agents see other agents' *conclusions* — one line plus the
number plus confidence — never their full outputs. Cross-agent context grows linearly and
slowly, instead of quadratically.

---

## 4. Efficiency budget

Enforced per role, logged per run on `AgentRun`, and asserted in CI so a prompt change that
doubles context fails the build.

| Component | Budget |
|---|---|
| System prompt (per agent, static) | < 800 tok |
| Context Pack | < 600 tok |
| Findings digest | < 400 tok |
| Tool results (hard row caps, pre-aggregated) | < 1500 tok |
| **Total input per agent call** | **< 4000 tok** |
| Output (structured JSON, no prose) | < 500 tok |

Techniques that get you there:

- **Structured output on every call.** Cuts output tokens hard and eliminates parsing
  failures. Non-negotiable for the spec's Agent Output Contract.
- **Tools return decisions, not data.** `rank_collection_opportunities(top_n=10)` returns
  ten pre-ranked rows with the arithmetic already done. Never return a table for the model
  to sum — it will sometimes get it wrong, and it costs tokens to be wrong.
- **Hard row caps on every tool.** Truncation is explicit and reported (`showing 10 of 847`),
  never silent.
- **No chain-of-thought in outputs.** The spec forbids exposing it; the budget forbids
  paying for it.
- **Stable prefix ordering** (tools -> system -> volatile) so provider-side caching can
  apply where available.
- **One task per call.** Two narrow calls on a small model beat one broad call, and they
  fail independently and retry cheaply.

---

## 5. Rate limits and the parallel wave

A free tier will throttle nine concurrent agents. Build for it rather than discovering it
in the demo:

- Bounded concurrency (configurable, default 3-4 in-flight), not unbounded `gather`.
- Token-bucket limiter shared across the process, sized from the tier's documented RPM.
- Exponential backoff with jitter on 429; per-agent timeout independent of retry budget.
- Queue depth and wait time surfaced as agent status — "AR Agent: queued" is honest UI.
- Degraded path already required by the spec: if an agent cannot run, the Commander
  proceeds with reduced confidence and flags the recommendation for human review.

The wave is *logically* parallel even when it is *physically* throttled. Nothing in the
architecture changes; only the executor's concurrency setting does.

---

## 6. Where a small model is fine, and where it is not

| Task | Small model | Notes |
|---|---|---|
| Table/column classification (Cartographer) | Fine | Narrow, tight schema, deterministic gate behind it |
| Covenant explanation | Fine | Numbers come from code; model only phrases them |
| Evidence summarization | Fine | Extractive, bounded |
| Agent findings from tool output | Fine | Tools pre-compute; model structures |
| Ranking qualitative trade-offs | Adequate | Give explicit criteria; do not leave it open-ended |
| **Commander investigation planning** | **Risk** | Multi-step, open-ended, consequential |
| **Conflict resolution between agents** | **Risk** | Requires holding several findings in tension |

Mitigations for the two risk rows, in order of preference:

1. **Constrain the choice.** The Commander picks from an enumerated set of investigation
   plans against a capability manifest — closer to classification than to free planning.
   This is the highest-leverage fix and costs nothing.
2. **Decompose.** Conflict resolution becomes: detect (deterministic) -> classify conflict
   type (narrow call) -> select follow-up from a fixed catalogue (narrow call).
3. **Route those two roles to a stronger model.** One line of YAML. Two roles at low volume
   is a small bill, and the interface already supports it.

Do 1 and 2 first; keep 3 available and measure before spending.

---

## 7. Testing and determinism

- `FakeProvider` replays recorded fixtures — the golden-path demo produces identical
  numbers every run, and the test suite costs nothing.
- Contract tests per agent: schema-valid output, evidence references resolve, refusal cases
  refuse (AP Agent asked to defer payroll returns a constraint violation).
- Token-budget assertions in CI.
- A small eval set per agent role, so a model swap is a measurement rather than a guess.
  This matters more than usual here: you are on a fast-moving model family, and
  `gemini-3.7-flash` will not be current for long.
