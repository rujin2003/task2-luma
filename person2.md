# Person 2 — Agentic Product

**You own:** the agent runtime, the six specialist agents, the Commander, the weekly cycle,
scenario/stress/replan, and the entire frontend.

**Your north star:** the product must be usable on a normal Monday, and the agentic
behaviour must be genuine — real conflicts from real data contradictions, resolved by
evidence. Never let an agent compute a number; make it call Person 1's engine.

**Read first:** `WORKFLOW.md` (defines the product), then `LLM_STRATEGY.md`, then `PHASES.md`.

---

## File ownership

**Yours — edit freely:**
```
backend/agents/**        backend/orchestrator/**   backend/api/**
frontend/**              tests/agents/**           tests/e2e/**
prompts/**
```

**Shared — change only by agreement with Person 1, both review the PR:**
```
backend/contracts/**     # Pydantic schemas
backend/tools/**         # tool signatures (they write the bodies, you call them)
config/**
```

**Theirs — do not edit:** `backend/models/**`, `backend/finance/**`, `backend/seed/**`,
`backend/integrations/**`, `backend/ingest/**`, `alembic/**`

Branch naming: `p2/<phase>-<topic>`. Small PRs. Never push to `dev` directly.

---

## Stage 0 — Phase 0: Foundation (with Person 1, day 1)

Do this **together, in one sitting.** This is the last time you both touch the same files
heavily, and everything after depends on getting it right.

Your half:
- Pydantic contracts in `backend/contracts/`: `AgentFinding`, `Evidence`, `Constraint`,
  `Strategy`, `WorklistItem`, `StressResult`, `Recommendation`, `ApprovalRequest`,
  `Override`.
- The SSE event schema for live agent activity — freeze it now so the UI can be built
  against it before any agent exists.
- Frontend scaffold: Next.js + TypeScript + Tailwind, typecheck in CI.

**Agreed jointly and then frozen:** the contracts above and the tool signatures in
`backend/tools/`. Person 1 writes the bodies; you code against the signatures from day one
with `NotImplementedError` stubs and their fixtures.

**Done when:** contracts validate, the event schema is frozen, frontend builds, CI green.

---

## Stage 1 — Phase 5: Agent Runtime + UI scaffold

You are **not blocked** on Person 1. Build against their fixture JSON in `tests/fixtures/`.
Chase them for it on day 2 if it hasn't landed.

### Phase 5 — Agent Runtime

- **`LLMProvider` interface** — provider-agnostic, per-role model routing from
  `config/models.yaml`. Two implementations: `GeminiProvider` and `FakeProvider` (replays
  recorded fixtures). The fake is what makes the demo deterministic and the test suite free
  — build it first.
- **Verify the model before building on it.** `gemini-3.1-flash` appears superseded (Google's
  docs banner shows 3.8 Flash; samples use `gemini-3.7-flash`). Run the models-list `curl`
  in `LLM_STRATEGY.md` §1 and record real context window, limits and function-calling
  support in that file. Also check free-tier retention/training terms before any real
  tenant data goes near the API — that is a procurement blocker if it's wrong.
- **The tool layer.** Thin typed wrappers over Person 1's engine. This is the single most
  important architectural constraint in the project: an agent cannot compute a covenant
  ratio, it can only call `get_covenant_status()`. Tools return *decisions*, pre-aggregated
  and pre-ranked, with hard row caps — never raw tables for the model to sum.
- **Context Pack assembler** — ~2 KB deterministic brief per call (company, policy,
  capabilities, incident, prior findings digest, task, tools, output schema). Context
  awareness lives here and in the tool layer, never in a big prompt.
- **Evidence validator** — any `evidence[].reference` must resolve to a real row, or the
  finding is rejected, not surfaced. This is how "never fabricate evidence" is enforced.
- **`AgentRun` persistence** — inputs, tool calls, outputs, tokens, latency, status, failure.
- **Async executor with bounded concurrency** — a free tier will throttle nine concurrent
  agents. Token bucket, backoff with jitter, per-agent timeout, "queued" as an honest status.
- Token budget enforcement per role, asserted in CI (`LLM_STRATEGY.md` §4).
- SSE event bus.

### Phase 9 (start) — Forecast screen scaffold

Build the primary screen against fixture JSON now. It is the analyst's home and the biggest
UI surface — starting it late is the main schedule risk on your track.

**Done when:** two mock agents run in parallel, one times out, both `AgentRun` rows are
complete, the failure surfaces as degraded status, and the Forecast screen renders fixtures.

---

## Stage 2 — Phase 6: The Six Agents

Still on fixtures. Build in this order:

1. **Forecast Agent** — explains each category's assumption, flags stale ones. Does *not*
   compute the forecast.
2. **Variance Agent** — root-causes each material delta with evidence to source rows.
   **This is the star.** It answers the question the Treasurer asks first, every week. If
   only one agent is excellent, make it this one.
3. **AR Collections Agent** — probability-weighted acceleration; emits ranked *worklist
   rows*, not an aggregate. Never assume all open AR is collectible.
4. **AP Optimization Agent** — deferral candidates with early-pay-discount cost quantified.
5. **Supplier Risk Agent** — adversarial by design; exists to reject AP proposals and must
   attach evidence when it does.
6. **Dodo Revenue Agent** — at-risk and recoverable collections derived from Dodo's
   documented soft/hard decline taxonomy.

Each ships with: prompt, tool allowlist, output schema, golden-input regression test, and a
**"must refuse" test** — the AP Agent asked to defer payroll returns a constraint violation,
not a plan.

Keep building the Forecast screen in parallel.

**Done when:** all six produce schema-valid, evidence-resolved findings; the Variance Agent
explains a seeded variance correctly; Supplier Risk rejects a specific AP proposal with
evidence attached.

---

## Stage 3 — Phase 7: Weekly Cycle + Merge Point 1

### 🔀 Merge Point 1 — with Person 1

Swap fixture tools for their real engine behind the same signatures. **Do a joint dry run
of one tool end-to-end during Stage 2**, well before you need all of them — that is when
contract drift surfaces cheaply.

### Phase 7 — Weekly Cycle

The ten-step Monday cycle from `WORKFLOW.md` §4. This is what makes the project a product
rather than a crisis simulator, and it is the phase judges will care most about.

- Scheduled refresh of actuals, classification, bank reconciliation display.
- Variance bridge generation with agent explanations for material items only.
- Driver refresh and reforecast; accuracy roll-forward.
- **Exception surfacing** — only what moved materially or where an assumption went stale.
- **Review & challenge** — the human judgement step. Analyst reviews, Treasurer approves.
  An **`Override` is a first-class recorded object** with a stated reason; an overridden
  assumption is better data than a generated one and feeds back into accuracy tracking.
- **Publish** — locks the `ForecastVersion`. It becomes next week's baseline.
- **Policy check** — a breach opens the war room on a specific, dated, quantified condition.
- Period and close-calendar awareness; never post across a closed period.

**Done when:** a full cycle runs end to end on real seeded data, produces a variance bridge
that ties, publishes a version, and a seeded shock trips escalation.

---

## Stage 4 — Phase 8 + Phase 9 completion + Phase 10 (frontend)

### Phase 8 — Commander, Scenarios, Stress, Replan

- **Dynamic investigation planning** driven by the tenant capability manifest. Must justify
  why an agent was *not* called — a test asserts a pure-AR incident does not invoke Dodo.
  **Select from an enumerated set of investigation plans** rather than free-form planning;
  more reliable on a small model, and it costs nothing.
- **Conflict detection** — structural (numeric disagreement beyond tolerance) and semantic
  (AP says defer, Supplier Risk says do not). Detection deterministic; resolution agentic.
- **Resolution by evidence** — scoped follow-up, not "pick the higher confidence".
- Investigation state machine, persisted and replayable. Bounded depth, replans, wall clock.
- **Worklist generation** — levers proposed by agents, composed and priced by deterministic
  code into bundles with owner, counterparty, document ref, amount, due date, status.
- **Rejected actions surfaced with reason and evidence.** An experienced treasurer looks
  for this first.
- Stress testing: math deterministic, stressor *selection* agentic, **calibrated to Person
  1's measured forecast error** — "AR at the 90th percentile of our own 26-week error", not
  an arbitrary −10%.
- Replan loop: failure reason feeds back, constraint tightened, attempt history preserved.

### Phase 9 — Frontend (four screens)

1. **Forecast** (primary) — 13-week grid, variance bridge, accuracy panel, exceptions
   queue, review/publish controls, liquidity summary as header.
2. **War Room** (live) — only present during escalation. Concise status lines.
   **No raw chain-of-thought** — findings, evidence, decisions, status only.
3. **Recommendation** — worklist, rejected actions with reasons, stress results, scenario
   comparison as a side-by-side matrix.
4. **Evidence Explorer** — click any number, walk provenance to the source ledger row.
   Build this early; it doubles as your debugging tool.

Visual grammar separating **actual / estimate / assumption / recommendation**. Persistent
synthetic-data banner. Degraded states as first-class UI, not error toasts.

### Phase 10 — Approvals (frontend half)

Approval card rendering exactly: ACTION / AMOUNT / EXPECTED IMPACT / RISK / EVIDENCE /
WHY RECOMMENDED / WHAT COULD GO WRONG / APPROVAL REQUIRED. Routing UI driven by Person 1's
DoA matrix.

**Done when:** every number on the Forecast screen is click-through traceable to a source row.

---

## Stage 5 — Phase 11: Hardening (with Person 1)

Yours: `FakeProvider` fixture recording so the golden path is byte-identical every run;
failure injection for LLM down, agent timeout, contradictory findings, missing evidence;
parallel agent wave under ~60s; demo script.

---

## What Person 1 is doing (your dependencies)

| Stage | They build | You need from them | They need from you |
|---|---|---|---|
| 0 | Money, provenance, policy, CI | frozen tool signatures | frozen contracts, event schema |
| 1 | Data model, seeding | **fixture JSON — chase this early** | — |
| 2 | Forecast engine, variance, accuracy | engine progress updates | — |
| 3 | Dodo integration | **real engine behind tool signatures** | joint dry run of one tool |
| 4 | Cartographer, DoA, audit log | DoA matrix, audit log API | agent runtime for their 4 sub-agents |
| 5 | Determinism harness | — | recorded LLM fixtures |

**Daily 15-minute sync.** The two things that can genuinely hurt you: fixtures arriving
late (chase on day 2), and starting the Forecast screen too late.
