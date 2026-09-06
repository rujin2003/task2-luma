# WAR ROOM

**Autonomous treasury crisis response.** A weekly 13-week rolling direct-method cash
forecast that, when it detects a policy breach, opens a war room: specialist agents
investigate in parallel, disagree with each other, propose liquidity strategies, and
stress-test them until one survives — then hand the CFO an evidence-backed plan that
nobody can execute without a signature.

The organising rule of the whole codebase:

> **Agents reason. Code computes.**
> No language model produces a number that reaches a financial decision. Agents choose
> what to investigate, interpret evidence, argue trade-offs and explain themselves. Every
> figure comes from deterministic, type-checked, float-free Python.

---

## Quickstart

```bash
python -m pip install -e ".[dev]"

make seed SEED=42      # deterministic NovaTech ledger
make demo              # the golden path, narrated, offline and free
```

`make demo` needs no API key and no network. It runs the real product — the same
`Session` the API serves — against recorded model completions, and prints what actually
happened. Nothing in it is scripted: if the orchestrator stops producing a step, the demo
prints less rather than pretending.

Run the UI:

```bash
make serve                                  # FastAPI on 127.0.0.1:8000
cd frontend && npm install && npm run dev   # Next.js on 127.0.0.1:3000
```

Verify everything:

```bash
make ci     # ruff, mypy (strict on backend/finance), the no-float gate, 722 tests
```

---

## The demo, in one screen

`make demo` walks the full incident. Real output, abridged:

```text
  Stress
  x financing-bridge      under Combined adverse case (-17.3%):  19,825,621 USD at W6
  v working-capital       under Combined adverse case (-17.3%):  20,086,221 USD at W6

  - AR recovery shortfall: 90th percentile of this company's own W6 error over 26 weeks

  Approvals
  ACTION               Defer BILL-8863 (Globex Logistics) by 30d
  AMOUNT               620,000.00 USD
  RISK                 not reversible without telling the counterparty
  EVIDENCE             ap_ledger:BILL-8863#due_date
  WHAT COULD GO WRONG  Under Combined adverse case an earlier bundle fell 174,378 USD
                       short of the floor; this row is subject to the same error.
  APPROVAL REQUIRED    cfo

  x preparer tried to sign their own card: analyst@novatech prepared this request
  v signed by cfo@novatech (cfo) against fv-2026-W10
```

Three things in that output are the point of the project:

1. **The first plan fails.** `financing-bridge` breaks the floor under stress, so the
   Commander replans and `working-capital` is what reaches the CFO. The failure is not
   staged — it is recomputed every run.
2. **The stressors are calibrated, not invented.** `-17.3%` is the 90th percentile of
   _this company's own_ week-6 forecast error across 26 weeks of history.
3. **Segregation of duties is enforced in code.** The preparer is refused their own
   signature. That is a hard failure, not a warning.

Run `python -m scripts.demo --break-llm` for the same Monday with **every model call
timing out**. The cycle still publishes, the war room still opens on the same breach, the
Commander still selects a named plan, and every fallback says so out loud.

---

## How it works

```text
  Weekly cycle (10 steps, 7 automated)
        |  refresh actuals -> variance bridge -> reforecast -> accuracy -> exceptions
        v
  Policy check ---- breach? ----> War room opens
                                       |
        +--------------+---------------+---------------+--------------+
        v              v               v               v              v
    Forecast       Variance      AR Collections   AP Optimization   Dodo Revenue
        +--------------+---------------+---------------+--------------+
                                       |          ^
                                 Supplier Risk ---+   (rejects AP's deferrals)
                                       |
                                  Conflict resolution
                                       v
                          Scenario bundles -> constraint check
                                       v
                              Stress test --> fail --> replan --+
                                       |                        |
                                       v<-----------------------+
                          Recommendation -> DoA routing -> approval -> execution
```

**Six specialists** — Forecast, Variance, AR Collections, AP Optimization, Supplier Risk,
Dodo Revenue — plus coordinating roles: Commander, Conflict Resolution, Stress Test,
Covenant Explainer, Cartographer. The Commander does not call all of them every time; it
decomposes the specific breach.

Supplier Risk exists to **disagree**. When it rejects an AP deferral, that cash genuinely
leaves the strategy, which genuinely changes whether the strategy survives stress. The
disagreement is load-bearing, not decorative.

---

## Financial integrity

This is the part built for a treasury reviewer rather than a demo audience.

| Guarantee             | How it is enforced                                                 |
| --------------------- | ------------------------------------------------------------------ |
| No floats in money    | `make check-float` — a build gate over 45 modules                  |
| Exact arithmetic      | `Money` in integer minor units with explicit currency              |
| Types                 | `mypy --strict` on `backend/finance`                               |
| Determinism           | seeded generation; identical dumps across runs (`tests/seed/`)     |
| Reproducible agents   | recorded completions replayed by request fingerprint               |
| Cost control          | per-role token budgets in `config/models.yaml`, asserted in CI     |
| Segregation of duties | preparer, reviewer and approver must be three people               |
| Provenance            | every figure traces to source, calculation, assumption, confidence |

Constraints are **hard** and declarative: payroll and tax cannot be delayed, minimum cash
and 30-day liquidity floors, revolver utilisation ceilings, per-supplier maximum delays.
They live in `config/treasury_policy.yaml`, not in an `if` statement.

Delegation of authority lives in `config/doa_matrix.yaml` as amount-banded routes. If an
edit makes an amount match two bands, `route_approval` **rejects it** rather than quietly
approving at the lower level.

---

## Failure is a first-class path

`tests/e2e/test_degraded_paths.py` asserts what happens when things break:

- every model call times out → the cycle still publishes, with named fallbacks
- Supplier Risk is missing → the loss of the adversary is _visible in the result_
- an agent returns malformed output → it is not a finding, and the investigation closes
- Dodo is unreachable → collections are not risk-adjusted, confidence drops, and the
  recommendation is flagged for human review

A degraded run still cannot be self-approved and still cannot skip execution gates.

---

## Dodo Payments

Dodo is a financial input, not a checkout button. `backend/integrations/dodo/` is a
**read-only** boundary — write endpoints are deliberately absent — covering the client,
Standard Webhooks signature verification, payout-lag modelling, a decline taxonomy and
success-rate metrics.

Declining payment success becomes at-risk collections, which changes the forecast, which
is what trips the breach that opens the war room. Nothing outside that package sees Dodo's
HTTP details; the rest of the system sees normalised `PaymentEvent`s.

---

## Bring your own ledger

`backend/ingest/` is the **Schema Cartographer**: point it at a database whose schema you
have never seen, and it introspects, matches against templates, reconciles, freezes a
mapping and then watches for drift.

```bash
make demo-companies                 # four tenants, four different schemas
make onboard TENANT=northgate       # walk the DB agent end to end
```

Only column names, types and format signatures are ever sent to a model — never financial
rows.

---

## Layout

```text
backend/finance/        Money, FX, policy, forecast engine, covenants, controls, audit
backend/contracts/      shared Pydantic contracts (agents + engine DTOs)
backend/tools/          tool protocol; EngineToolset (real) and FixtureToolset (recorded)
backend/agents/         six specialists, Gemini and replay providers, routing, budgets
backend/orchestrator/   weekly cycle, investigation, conflicts, stress, replan, approvals
backend/integrations/   Dodo adapter (webhooks, payout lag, metrics, decline taxonomy)
backend/ingest/         Schema Cartographer and mapping freeze
backend/models/         SQLModel entities + alembic migrations
backend/seed/           deterministic NovaTech generator and shock packs
backend/api/            FastAPI: cycle, war-room SSE, recommendation, approvals, evidence
frontend/               Next.js: Forecast, War Room, Recommendation, Evidence, Approvals
config/                 treasury policy, DoA matrix, scoring weights, model routing
tests/                  finance, seed, tools, agents, ingest, integrations, api, e2e
docs/                   workflow, phases, LLM strategy, schema adaptation
```

Read `docs/WORKFLOW.md`, then `docs/PHASES.md`, before changing a contract.

---

## Configuration

| Variable         | Meaning                                                             |
| ---------------- | ------------------------------------------------------------------- |
| `WARROOM_LLM`    | `replay` (offline, deterministic — CI and demo), `gemini`, or `auto` |
| `GEMINI_API_KEY` | required for `gemini`; `auto` falls back to `replay` without it      |
| `DATABASE_URL`   | defaults to `sqlite:///novatech.db`; Postgres supported via psycopg  |

Copy `.env.example` to `.env`. `make gemini-smoke` verifies a live model before a demo.

Model ids, temperatures, per-role token budgets and executor concurrency all live in
`config/models.yaml` — swapping providers is a change to that file and nothing else.

---

## Demo data is synthetic

NovaTech is fictional: ~$450M revenue, multiple entities, currencies, bank accounts,
customers, vendors, a revolving facility and covenants. The data is internally consistent
— every invoice ties to a customer, a payment history, an AR balance and a forecast
contribution — with deliberate anomalies injected as shock packs.

No real financial data is present anywhere in this repository.
