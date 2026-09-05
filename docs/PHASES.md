# WAR ROOM — Implementation Phases

Derived from the project spec (`CLAUDE.md` on `dev`), **re-anchored on the workflow finance
teams actually run weekly**. Read `WORKFLOW.md` first — it defines the product; this file
sequences the build.

**The re-anchor.** The product is the **weekly 13-week rolling direct-method cash forecast
cycle**. The war room is the escalation path that opens when that cycle detects a policy
breach. Nothing agentic is lost — the multi-agent investigation, conflict resolution,
stress testing and replanning all survive as the escalation branch. What changes is that
the system now has a reason to be opened on a normal Tuesday.

**Two sequencing principles:**
1. *Deterministic engine first, agents second, UI third.* Agents must never compute a
   number they could instead call for.
2. *Depth over breadth.* One workflow modelled properly beats six modelled shallowly.

---

## What we deliberately cut, and why

Scope removed from the original spec. Each of these was costing build time without
strengthening the workflow.

| Cut | Reason |
|---|---|
| **FX Agent** | Contributes nothing to a 13-week forecast beyond translating known obligations — that is deterministic code. Spec also forbids it from doing the only interesting thing (rate views). Keep `fxconv.py`; drop the agent, `FXPosition`, `FXHedge`. |
| **Debt Agent** | "Compute available capacity" is arithmetic against a facility table. No judgement. Becomes `debt.py` in the engine, exposed as a tool. |
| **Covenant Agent** | The spec already requires it to be deterministic with the LLM only explaining. That is an engine module plus a rendering function, not an agent. |
| **Subsidiaries & intercompany** | Significant modelling cost, no contribution to the demo narrative. Single entity in v1. |
| **Multi-currency NovaTech** | Single currency (USD). Realism comes from workflow fidelity, not entity sprawl. Currency stays explicit in the schema for tenants who need it. |
| **Standalone Scenario Comparison screen** | Folds into the Recommendation screen. |
| **Standalone Executive Overview screen** | Becomes the summary header of the Forecast screen — the analyst's home, not a separate destination. |
| **"Strategies" as the output** | Replaced by **worklists**: owner, counterparty, document reference, amount, due date, status. Finance people approve transactions, not abstractions. |
| **LLM self-reported confidence** | Replaced by **backtested forecast accuracy** (MAPE by category and horizon). `0.87` is unfalsifiable; measured error is not. |
| **"Primary user is the CFO"** | Wrong persona. The **Treasury Analyst** operates it, the Treasurer reviews, the CFO approves. CFOs consume outputs; they do not operate systems. |

Agent roster: **12 → 6.** Commander, Forecast, Variance, AR Collections, AP Optimization,
Supplier Risk, Dodo Revenue, Stress Test (thin — the math is deterministic, only stressor
*selection* is agentic).

---

## What we added

| Added | Criterion it answers |
|---|---|
| 13-week rolling direct-method forecast with real cadences | genuine daily pain point |
| **Variance bridge** (forecast vs actual, forecast vs prior forecast) | the artifact treasury actually argues over |
| **Forecast accuracy tracking** (MAPE by category × horizon) | credibility; honest stress calibration |
| **Worklist** — actions as executable transactions | intuitive human judgement |
| **Delegation of authority matrix** + maker-checker + materiality | real-world controls; SOX-relevant |
| **Bank reconciliation artifact** with sign-off block | the accountant-facing output |
| Period / close-calendar awareness and cut-off | accountants work in periods |

---

## Phase Map

| # | Phase | Deliverable | Depends on |
|---|-------|-------------|-----------|
| 0 | Foundation & Contracts | repo, CI, schemas, money type | — |
| 1 | Financial Data Model | Postgres + SQLModel, forecast/variance/worklist entities | 0 |
| 2 | Data Ingestion & Seeding | real-data-derived NovaTech + **26 weeks of forecast history** | 1 |
| 2A | Onboarding & Schema Adaptation | Schema Cartographer, tenant self-serve onboarding | 1, 2 |
| 3 | **Forecast Engine** | 13-week direct method, variance bridge, accuracy, covenants, constraints | 1, 2 |
| 4 | Dodo Payments Integration | live payment signal + webhooks | 1 |
| 5 | Agent Runtime & Contracts | tool layer, structured outputs, evidence, model routing | 3, 4 |
| 6 | Specialist Agents | 6 agents, parallel execution | 5 |
| 7 | **Weekly Cycle & Escalation** | Monday cycle, exceptions, review/publish, breach trigger | 6 |
| 8 | Commander, Scenarios, Stress & Replan | investigation, conflict resolution, worklist generation | 3, 7 |
| 9 | Frontend | Forecast (primary), War Room, Recommendation, Evidence Explorer | 7, 8 |
| 10 | Controls & Approvals | DoA matrix, maker-checker, materiality, audit trail | 8, 9 |
| 11 | Golden-Path Demo & Hardening | determinism, failure modes, E2E | all |

---

## Phase 0 — Foundation & Contracts

**Goal:** make incorrect money arithmetic structurally impossible before any feature
code exists.

Scope:
- Monorepo: `backend/` (FastAPI + Python 3.12), `frontend/` (Next.js + TS + Tailwind), `docs/`.
- `Money` value type: integer minor units + explicit ISO-4217 currency. No `float`
  anywhere in the financial path. Add a CI rule that fails on `float` in
  `backend/finance/**`.
- Core Pydantic contracts up front, because every later phase depends on them:
  `AgentFinding`, `Evidence`, `Confidence`, `Constraint`, `Strategy`, `StressResult`,
  `Recommendation`, `ApprovalRequest`.
- `Provenance` primitive: `(source_system, record_id, field, as_of, retrieved_at)`.
  Nothing enters the system without one. This makes "never fabricate evidence"
  enforceable by the type system rather than by prompt instruction.
- CI: pytest, ruff, mypy strict on `finance/`, frontend typecheck.
- Config layer: all policy thresholds (min cash, min 30d liquidity, max revolver
  utilization, protected payment classes) live in a versioned `TreasuryPolicy` row,
  never in code.

**Exit criteria:** `Money` passes property tests for add/subtract/allocate/convert with
no rounding leakage; a `TreasuryPolicy` loads and validates.

---

## Phase 1 — Financial Data Model

**Goal:** the spec's entities, with referential integrity that makes inconsistent data
unrepresentable.

Scope:
- Entities, revised against the cut list — `Subsidiary`, `FXPosition` and `FXHedge` are
  dropped from v1:
  `Company`, `BankAccount`, `BankTransaction`, `BankReconciliation`, `GLAccount`,
  `GLTransaction`, `Customer`, `Invoice`, `Payment`, `Vendor`, `VendorInvoice`,
  `PaymentRun`, `DebtFacility`, `DebtCovenant`, `Subscription`, `FinancialEvent`,
  `Scenario`, `StressTest`, `Recommendation`, `Approval`, `AgentRun`, `Evidence`.
- New entities the re-anchor requires:
  `ForecastVersion` (published, immutable, the variance baseline), `ForecastLine`
  (category × week, with source/assumption/method), `VarianceItem`, `AccuracyStat`
  (category × horizon × window), `Override` (assumption, reason, author),
  `WorklistItem` (owner, counterparty, document ref, amount, due date, status),
  `ApprovalRoute` (DoA band), `CalendarEvent` (pay dates, tax dates, AP runs, holidays),
  `AccountingPeriod` (open/closed, cut-off).
- Every monetary column stored as `BIGINT` minor units + `CHAR(3)` currency, always paired.
- Bitemporal columns on anything an agent will cite: `effective_at` and `recorded_at`.
  Without these you cannot answer "what did we know when we made this recommendation",
  which is the entire premise of the Evidence Explorer screen.
- `is_synthetic BOOLEAN NOT NULL` on every fact table — the spec requires demo data to be
  distinguishable from production data, and that needs to be queryable, not just visual.
- Append-only `FinancialEvent` ledger; entity tables are projections of it.
- Alembic migrations from day one.

**Exit criteria:** seed a trivial 3-invoice company; bank balance, AR balance and GL cash
account all reconcile via SQL alone.

---

## Phase 2 — Data Ingestion & Seeding

**Goal:** a NovaTech dataset *derived from real-world data* rather than invented.

See `DATA_SOURCES.md` for the verified source list — real FX rates, real interest-rate
curves, real payment-behaviour distributions from public invoice datasets, real covenant
language and ratio conventions from SEC filings.

Scope:
- Pluggable `SourceAdapter` interface; one adapter per external dataset.
- Deterministic generator: a single integer seed produces an identical dataset every run.
  Randomness is used only to *sample from empirically observed distributions*, never to
  invent a number.
- Consistency invariants enforced at generation time and re-checked by tests:
  - sum of open invoices = AR balance = GL AR control account
  - sum of vendor invoices due = AP balance
  - sum of bank transactions = bank balance = GL cash (minus deliberately injected variance)
  - every Dodo payment event maps to exactly one `Invoice` or `Subscription`
  - every forecast line traces to a ledger row
- **26 weeks of prior forecast versions**, not just a current state. Without forecast
  history there is nothing to backtest, and forecast accuracy — which replaces LLM
  confidence throughout the product — cannot be computed. This is the single most
  commonly missed seeding requirement.
- Real cadences, not averages: dated pay runs (including the two months with three
  biweekly payrolls), Thursday AP runs, 1st-of-month rent, quarterly estimated taxes,
  weekday-weighted receipts, bank-holiday settlement shifts. See `WORKFLOW.md` §3.
- Anomaly injection as an explicit, named, replayable pack rather than random noise:
  `customer_delay_4m`, `dodo_success_rate_drop`, `supplier_acceleration_1m`,
  `eur_obligation_increase`, `bank_gl_variance`, `covenant_headroom_squeeze`,
  `stale_forecast_assumption`.

**Exit criteria:** `make seed SEED=42` twice produces byte-identical DB dumps; all
consistency invariants pass; the shock pack applies and rolls back cleanly.

---

## Phase 2A — Onboarding & Schema Adaptation Engine

**Goal:** a startup connects its own database and reaches a working Executive Overview in
minutes, with no code written for them.

Full design in `SCHEMA_ADAPTATION.md`. Summary of what this phase builds:

- **Schema Cartographer** — a seven-stage pipeline (connect -> introspect -> template match
  -> agent proposal -> validate -> confirm -> freeze -> drift watch). Six stages are
  deterministic; exactly one calls a model, and only on what the deterministic stages could
  not resolve.
- **Raw landing zone** — per-tenant `raw.*` schema, stored verbatim, never mutated. This is
  the bottom of the Evidence Explorer's provenance tree.
- **Declarative mappings** — versioned `mapping.vN.yaml` per tenant. After freeze there is
  **no model in the runtime path**; ingestion is deterministic, replayable ETL.
- **Template library** — known schema shapes (QuickBooks, Xero, NetSuite, Odoo, Stripe,
  Dodo, common SaaS billing layouts). This is the scalability mechanism: model spend is per
  novel *schema shape*, not per tenant, and confirmed mappings are contributed back
  (column names, types and roles only — never values).
- **Canonical account roles** — ~15 roles replace per-company GL account numbers.
- **Reconciliation gate** — a mapping is accepted only if AR, AP, cash and GL tie out, with
  an explicit coverage score for rows and monetary value. A wrong mapping fails
  arithmetically, which is what makes it safe for a small model to propose one.
- **Capability manifest** — per-tenant declaration of which domains exist (fx, debt, gl,
  payroll calendar). Feeds Phase 7's dynamic agent selection directly.
- **Multi-tenancy** — `tenant_id` everywhere, Postgres RLS, per-tenant policy, per-tenant
  encrypted credentials, per-tenant sync queues so one broken mapping cannot stall others.

**Security posture:** read-only database roles, and **no raw financial rows are ever sent
to a model** — the Cartographer sees column names, types and format signatures only. State
this explicitly in onboarding; for a treasury product it is likely a hard procurement
requirement.

**Exit criteria:** three structurally different source schemas (a Stripe+QuickBooks stack,
a bespoke Postgres billing schema, and a CSV upload) each onboard to a reconciling mapping;
the template-matched case completes with zero model calls; onboarding SLO is under 10
minutes for a matched schema.

---

## Phase 3 — Forecast Engine

**Goal:** every number the user ever sees is computed here, deterministically, and is
unit-tested. Agents may only *read* from this engine.

Scope:
- `forecast.py` — **13-week rolling direct-method forecast**, weekly buckets, fixed
  category set (`WORKFLOW.md` §3). Every line carries source, assumption, method and
  as-of. Rolling: drop week 1, add week 14.
- `cadence.py` — the calendar layer that makes it real: pay-date generation (semi-monthly
  and biweekly), payroll-tax deposit schedules, AP run days, quarterly estimated tax dates,
  debt service dates, weekday weighting, bank holidays and settlement shifts.
- `variance.py` — **the bridge.** Forecast vs actual for the closed week, and forecast vs
  prior forecast for weeks 1-12, decomposed by category, gated by materiality.
- `accuracy.py` — **MAPE by category × horizon over a trailing window.** This replaces
  LLM-reported confidence everywhere in the product, and calibrates the stress scenarios
  in Phase 8 to the company's own observed error distribution.
- `cash.py` — aggregation, restricted vs unrestricted, and the **bank reconciliation
  artifact** (`WORKFLOW.md` §10): book balance, bank balance, reconciling items, aged
  exceptions, sign-off block.
- `covenants.py` — Net Debt/EBITDA, revolver utilization, min-cash, min-liquidity, with
  the real test date and definition, not a continuously evaluated ratio.
- `debt.py` — facility capacity, draw cost, amortisation (absorbed from the cut Debt Agent).
- `constraints.py` — hard-constraint validator returning structured pass/fail with the
  violated constraint and the margin. Payroll and tax delays forced to zero via policy.
- `materiality.py` — threshold resolution, used by variance, exceptions and review routing.

**Exit criteria:** golden-file tests over the seeded dataset; a forecast reproduces a
hand-worked 13-week grid exactly; the variance bridge for a known week ties to zero;
MAPE computes against the 26 weeks of seeded history; 100% branch coverage on
`covenants.py`, `constraints.py` and `cadence.py`.

---

## Phase 4 — Dodo Payments Integration

**Goal:** Dodo as a first-class treasury signal, isolated behind an adapter.

Endpoint surface verified against current Dodo docs — see `DATA_SOURCES.md`.

Scope:
- `integrations/dodo/` — client, DTOs, normalizer, webhook receiver. Nothing outside this
  directory knows Dodo exists; the rest of the system sees `PaymentEvent`.
- Ingest both directions: backfill via List Payments / Subscriptions / Refunds / Disputes
  / Payouts + Balance Ledger, and live via webhooks with signature verification and
  idempotent replay.
- **Payout timing is the detail most teams miss:** a successful payment is not cash.
  Model the payment -> balance-ledger -> payout lag explicitly, because that lag is exactly
  what moves the 30-day liquidity number.
- Derived metrics: rolling success rate, failure-reason taxonomy (soft vs hard decline,
  per Dodo's documented transaction-failure codes), recoverable-failure estimate, dispute
  reserve, expected collections with a confidence band.
- Graceful degradation: Dodo unavailable -> last-known forecast, reduced confidence,
  recommendation flagged for human review.

**Exit criteria:** a test-mode account replays a payment, a failure, a refund and a
dispute end-to-end into the forecast; killing the Dodo client degrades rather than crashes.

---

## Phase 5 — Agent Runtime & Contracts

**Goal:** the substrate agents run on. No domain agents yet.

Scope:
- Provider-agnostic agent interface. Agents receive **tools**, not database handles.
- The tool layer is a thin typed wrapper over the Phase 3 engine. This is the key
  architectural decision of the project: an agent cannot compute a covenant ratio, it can
  only call `get_covenant_status()`. That single constraint is what actually delivers the
  spec's "use deterministic code for financial arithmetic" rule.
- Every invocation persists an `AgentRun`: inputs, tool calls, outputs, tokens, latency,
  status, failure reason.
- Structured output enforcement with schema validation and bounded retry.
- Evidence validator: any `evidence[].reference` must resolve to a real row. An
  unresolvable reference means the finding is rejected, not surfaced — the enforcement
  point for "never fabricate evidence".
- Limited context per agent: each gets a scoped view, not the whole ledger.
- Async parallel executor with per-agent timeout, cancellation, partial-result handling.
- SSE/WebSocket event bus so Phase 9 can stream agent activity live.
- **Provider-agnostic `LLMProvider` interface with per-role model routing** (config, not
  code) plus a `FakeProvider` that replays recorded fixtures — this is what makes the
  golden-path demo deterministic and the test suite free. Default target is Gemini Flash;
  see `LLM_STRATEGY.md` for model verification, the per-agent token budget, the Context
  Pack design, rate-limit handling for the parallel wave, and which two roles carry real
  risk on a small model.
- **Context Pack assembler** — a ~2 KB deterministic brief per agent call. Context
  awareness lives in the tool layer and this brief, never in a large prompt.
- **Token budget enforcement** — per-role input/output caps logged on `AgentRun` and
  asserted in CI, so a prompt change that doubles context fails the build.

**Exit criteria:** two mock agents run in parallel, one times out, both `AgentRun` rows
are complete, and the failure surfaces as a degraded status rather than an exception.

---

## Phase 6 — Specialist Agents

**Goal:** six agents, each independently testable. Down from twelve — see the cut list.

1. **Forecast Agent** — explains each category's assumption and flags stale ones. Does not
   compute the forecast; the engine does.
2. **Variance Agent** — root-causes each material delta in the bridge, with evidence to
   source rows. *This is the star of the weekly cycle* — it answers the question the
   Treasurer asks first, every week.
3. **AR Collections Agent** — probability-weighted acceleration using empirical collection
   curves; emits ranked worklist rows, not an aggregate.
4. **AP Optimization Agent** — deferral candidates with early-pay-discount cost quantified;
   protected classes blocked upstream by policy.
5. **Supplier Risk Agent** — adversarial by design; exists to reject AP proposals and must
   attach evidence when it does.
6. **Dodo Revenue Agent** — at-risk and recoverable collections, derived from Dodo's
   documented soft/hard decline taxonomy rather than an invented recovery rate.

Plus the **Commander** (Phase 8) and a thin **Stress Test Agent** whose math is
deterministic — only stressor *selection* is agentic.

Each ships with: prompt, tool allowlist, output schema, golden-input regression test, and a
"must refuse" test (the AP Agent asked to defer payroll returns a constraint violation, not
a plan).

**Exit criteria:** all six produce schema-valid, evidence-resolved findings; the Variance
Agent explains a seeded variance correctly; Supplier Risk demonstrably rejects a specific
AP proposal with attached evidence.

---

## Phase 7 — Weekly Cycle & Escalation

**Goal:** the recurring workflow that makes this a product rather than a crisis simulator.

Scope — the ten-step Monday cycle from `WORKFLOW.md` §4:
- Scheduled refresh of actuals; classification to forecast categories; bank reconciliation.
- Variance bridge generation, materiality-gated, with agent explanations for material items.
- Driver refresh (AR aging, AP open items, Dodo events, calendars) and reforecast.
- Accuracy roll-forward.
- **Exception surfacing** — only what moved materially or where an assumption went stale.
- **Review & challenge** — the human judgement step. Analyst reviews, Treasurer approves.
  An **override is a first-class recorded object** with a stated reason; an overridden
  assumption is better data than a generated one, and it feeds back into accuracy tracking.
- **Publish** — locks the forecast version. It becomes next week's comparison baseline.
- **Policy check** — minimum cash, 30-day liquidity, covenant headroom. A breach opens the
  war room on a specific, dated, quantified condition.
- Period and close-calendar awareness; never post across a closed period.

**Exit criteria:** a full cycle runs end to end against seeded data, produces a variance
bridge that ties, publishes a version, and a seeded shock trips the policy check into
escalation.

---

## Phase 8 — Commander, Scenarios, Stress Test & Replanning

**Goal:** the escalation branch — dynamic investigation, genuine disagreement, and
worklists that survive stress testing.

**Commander** (absorbed from the original Phase 7):
- **Dynamic investigation planning** driven by the tenant capability manifest; must justify
  why an agent was *not* called. Prefer selecting from an enumerated set of investigation
  plans over free-form planning — more reliable on a small model, and it costs nothing.
- **Conflict detection** — structural (numeric disagreement beyond tolerance) and semantic
  (AP says defer, Supplier Risk says do not). Detection is deterministic; resolution is
  where the model earns its place.
- **Resolution by evidence** — scoped follow-up investigation, not "pick the higher
  confidence". Resolution rationale recorded.
- Investigation state machine, persisted and replayable; bounded follow-up depth, replan
  count and wall clock.

**Scenarios, stress and replan:**

Scope:
- Propose *levers*; deterministic code composes and prices them into **worklists** —
  owner, counterparty, document reference, amount, due date, status (`WORKFLOW.md` §8).
  At least four materially different bundles, different risk shapes.
- **Rejected actions are surfaced with their reason and evidence.** An experienced
  treasurer looks for this first.
- **Constraint gate** (Phase 3) filters infeasible strategies before stress testing.
- **Stress Test Agent:** named, parameterized stressors — AR recovery shortfall, revenue
  decline, payment-failure spike, FX shock, unexpected tax, supplier acceleration,
  financing-cost increase, customer default — plus a combined case. Stress math is
  deterministic; stressor *selection* is agentic.
- **Stressors are calibrated to the company's own measured forecast error** from Phase 3.
  "AR at the 90th percentile of our observed 26-week error" is defensible; "AR −10%" is
  arbitrary.
- **Scoring** against the spec's multi-objective function (shortfall, financing cost,
  supplier disruption, customer relationship, FX, covenant, operational risk) with
  configurable weights. Surface the weights in the UI — a hidden weight vector is an
  unauditable recommendation.
- **Replan loop:** a stress failure feeds the specific failure reason back to the
  Commander, which replans with that constraint tightened. Bounded iterations, with the
  full attempt history preserved for the demo narrative.

**Exit criteria:** the golden path deterministically produces a strategy that *fails*
stress, triggers a replan, and yields one that passes — same numbers every run.

---

## Phase 9 — Frontend Command Center

**Goal:** a CFO console, not a chat window.

Screens — four, not five:
1. **Forecast** (primary, the analyst's home) — the 13-week grid, variance bridge, accuracy
   panel, exceptions queue, review/publish controls, with the liquidity summary as its header.
2. **War Room** (live) — only present during an escalation.
3. **Recommendation** — worklist, rejected actions with reasons, stress results, scenario
   comparison folded in as a side-by-side matrix.
4. **Evidence Explorer** — click any number, walk provenance to the source ledger row.

Scope:
- Live agent activity stream over SSE, with concise status lines per the spec's Agent UX
  section. **No raw chain-of-thought** — findings, evidence, decisions, status only.
- Evidence Explorer: click any number and walk the provenance tree down to the source
  ledger row. This is the highest-value screen in the product and should be built early in
  the phase, not last — it doubles as the team's own debugging tool.
- Visual grammar separating **actual / estimate / assumption / recommendation**, applied
  consistently through colour and iconography.
- Persistent synthetic-data banner.
- Scenario comparison as a side-by-side matrix with stress rows.
- Degraded states as first-class UI, not error toasts.

**Exit criteria:** every number on Executive Overview is click-through traceable to a
source row.

---

## Phase 10 — Human-in-the-Loop & Approvals

**Goal:** nothing consequential happens silently.

Scope:
- **Delegation of authority matrix** (`WORKFLOW.md` §9) — approval routing by action type
  and amount band across Analyst / Treasurer / CFO / Board. Not a binary flag.
- **Segregation of duties / maker-checker** — preparer != reviewer != approver, enforced in
  code. SOX-relevant and non-negotiable in any audited company.
- **Materiality thresholds** — configurable; drive which variances are explained, which
  exceptions surface, and what needs review at all.
- Risk classifier using deterministic rules, not an LLM: reversibility x magnitude x
  counterparty impact -> `auto_safe` | `requires_approval` | `blocked`.
- Approval card rendering exactly the spec's fields: ACTION / AMOUNT / EXPECTED IMPACT /
  RISK / EVIDENCE / WHY RECOMMENDED / WHAT COULD GO WRONG / APPROVAL REQUIRED.
- Immutable audit log: who approved, when, what they saw, which app version, which data
  snapshot.
- Execution adapters behind an interface, with dry-run as the default. Debt draws, large
  FX and material supplier delays stay approval-gated regardless of configuration.

**Exit criteria:** a debt draw cannot execute without a recorded approval — verified by a
test that attempts it and expects a hard failure. A preparer cannot approve their own
worklist. An action $10 over a DoA band routes one level higher.

---

## Phase 11 — Golden-Path Demo & Hardening

Scope:
- One-command demo: reset -> shock -> full war-room run -> recommendation.
- Determinism harness: same seed produces an identical narrative and identical numbers.
- Failure injection suite: Dodo down, LLM down, agent timeout, incomplete bank data,
  contradictory findings, missing evidence, invalid scenario. Each has a defined, tested
  degraded output.
- Full three-level test pyramid per the spec (unit / agent / E2E).
- Performance: the parallel agent wave completes under roughly 60s wall clock.
- Docs: architecture, data provenance, agent catalogue, demo script.

**Exit criteria:** the golden path runs green ten consecutive times with identical output.

---

## Cross-Cutting Risks

| Risk | Mitigation |
|---|---|
| Agents "reason" their way to wrong arithmetic | Phase 5 tool layer — agents physically cannot compute, only call the engine |
| Fabricated evidence references | Evidence validator rejects unresolvable references before they reach the user |
| Non-deterministic demo | Fixed seed, temperature 0, cached LLM responses in demo mode, golden-file assertions |
| Theatrical conflict (agents disagreeing for show) | Conflicts must originate from a real data contradiction planted in Phase 2 |
| Dodo API drift | All Dodo knowledge confined to the Phase 4 adapter; contract tests against test mode |
| UI scope creep | Evidence Explorer and War Room first; everything else is negotiable |
| Tenant schema drift silently corrupting data | Stage 7 drift watch holds ingestion when reconciliation breaks, rather than importing |
| Onboarding model cost scaling with customers | Template matching + library contribution; spend is per novel schema shape, not per tenant |
| Small model unreliable on open-ended planning | Constrain to enumerated plans, decompose conflict resolution, keep per-role routing to a stronger model available |
| Free-tier data-handling terms block enterprise sales | Verify retention/training terms before tenant data reaches the API; no raw rows sent to a model by design |

---

## Suggested Sequencing

Phases 0-3 are strictly sequential and are the load-bearing part of the project. Phase 4
can run in parallel with 2-3 (different surface area). Phases 5-8 are sequential. Phase 9
can start against fixture data once Phase 5's event schema is frozen.

Phase 2A depends on the canonical model (Phase 1) and shares the consistency-invariant
machinery with Phase 2, so build it directly after — the reconciliation gate is the same
code the seed generator already needs. It is the difference between a demo and a product,
but it is not on the golden-path critical line: Phases 3-11 can proceed against the seeded
NovaTech dataset while 2A is built in parallel.

Budget roughly 40% of total effort to phases 0-3. Teams building this kind of system
usually invert that ratio and then discover their agents are confidently citing numbers
that do not reconcile.
