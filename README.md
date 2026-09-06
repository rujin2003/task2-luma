# WAR ROOM

Weekly 13-week rolling direct-method cash forecast, with a war room escalation when the
cycle detects a policy breach.

Merged Person 1 spine + Person 2 agents/frontend:

- `backend/finance/` — Money, cadence, forecast, variance, accuracy, cash, covenants, controls, audit, execution
- `backend/models/` + `alembic/` — SQLModel entities and migrations
- `backend/seed/` — deterministic NovaTech generator (`make seed SEED=42`) with 26-week forecast history
- `backend/tools/` — real `EngineToolset` behind the agent tool protocol
- `backend/integrations/dodo/` — client, webhooks (Standard Webhooks), payout lag, metrics, decline taxonomy
- `backend/ingest/` — **DB Agent**: connect → introspect → classify → map → reconcile → freeze → load → drift
- `backend/agents/` + `backend/orchestrator/` — six specialists, weekly cycle, Commander path, stress/replan
- `backend/api/` + `frontend/` — Data Source, Forecast, Agents, War Room, Recommendation, Evidence screens

## Setup

```bash
python3 -m pip install -e ".[dev]"
cp .env.example .env   # set GEMINI_API_KEY; WARROOM_LLM=gemini|fake|auto
make ci
make seed SEED=42
make demo
```

```bash
# The four demo tenants' source databases (four different schemas)
make demo-companies

# API (cycle, SSE, recommendation, approvals) — uses Gemini when WARROOM_LLM=gemini
make serve

# UI (Forecast / War Room / Recommendation / Evidence)
cd frontend && npm install && npm run dev
```

`make demo` and the test suite always use `FakeProvider` recordings (deterministic, free).
Live Gemini is only used by `make serve` / the API session when `WARROOM_LLM` is `gemini`
or `auto` with `GEMINI_API_KEY` set.

`make demo` resets a SQLite DB, seeds NovaTech, applies the shock pack, and asserts the
golden spine narrative in `tests/fixtures/demo/`. It also runs the deterministic war-room
path (fixture tools + FakeProvider): breach → investigation → conflict → stress failure →
replan → recommendation.

`make ci` runs ruff, mypy (strict on `backend/finance`), the no-float gate, and pytest.

## The DB Agent

A prospect's POC pastes their database URL — credentials included — and the agent works
out what the schema means, then loads it into our model. Eight stages, one of which may
call a model:

```text
connect → introspect → classify → map → reconcile → freeze → load → drift
```

* **connect** probes the credential and reports whether it can write. Ingestion stays
  read-only regardless; write capability is for the execution path later.
* **introspect** reads structure only — names, types, keys, row counts, and format
  *shapes* sampled from text columns. No financial value leaves the tenant.
* **classify** is a deterministic lexicon (`backend/ingest/columns.py`): token synonyms
  plus a type gate, scored in basis points. `cust_code`, `client_code` and `customerCode`
  all resolve to the same canonical field. A model is called only on the residue.
* **reconcile** holds the mapping to the tenant's own trial-balance totals. A mapping
  that does not tie out is rolled back rather than committed — wrong mappings fail
  arithmetically, which is what makes it safe for a small model to propose one.
* **drift** re-hashes the schema on every sync and holds ingestion when it changes.

The credential is held in memory for the length of the onboarding and never persisted,
logged or echoed; responses carry a redacted URL.

```bash
make demo-companies              # build the four demo source databases
make onboard TENANT=northgate    # walk all eight stages on the command line
```

### Demo tenants

Four companies, four genuinely different source schemas, so a successful mapping proves
something about a schema nobody shipped:

| Tenant | Schema style | Amounts |
|---|---|---|
| Helios Robotics | SAP-flavoured ERP (`ar_open_items`, `gross_amt`, `due_dt`) | decimal major units |
| Lumen Health Systems | Stripe / QuickBooks export (`invoices`, `amount_due`) | decimal major units |
| Northgate Freight | legacy warehouse (`receivable_ledger`, `amount_cents`) | integer minor units |
| Aurora Retail Group | camelCase app DB (`openReceivables`, `grossAmount`) | decimal major units, multi-currency |

## One data source, or nothing

Every screen in the product is a view of one data source, and there is no default. Before
a source is loaded the Forecast, Agents, War Room, Recommendation, Approvals and Evidence
screens all render their empty state and the agent roster refuses to start anything —
`POST /api/agents/<role>/run` is a 409, not an empty finding.

Loading one is the only way to fill them:

| | |
|---|---|
| **A tenant** | Walk the DB Agent on `/onboarding`. A load that reconciles becomes the source. |
| **The recorded demo** | One button on the same screen. NovaTech, deterministic, labelled synthetic everywhere it appears. |

Binding a source **clears everything the last one produced** — the cycle, the agent runs,
the investigation, the approval cards and the audit log go with it. That is enforced by
constructing a new `Session` (`backend/api/session.py:bind`) rather than by resetting
fields one at a time, so there is no half-cleared state to get wrong, and it happens on
*connect* rather than on load: a half-finished onboarding cannot leave the previous
tenant's numbers on screen either.

`GET /api/data-source` answers "what am I looking at" and is the first call every screen
makes. `GET /api/data-source/position` is the shared position — liquidity, the 13-week
series, aging, drivers, constraints and the capability manifest — read through the tool
layer, so the number on the Forecast screen is the number the Cash Forecast agent argues
about, from the same code path.

## Agents over a tenant's own ledger

`backend/tools/tenant.py` is the tool layer over rows the DB Agent has just loaded. A
source database gives six entities — customers, vendors, invoices, vendor invoices, bank
accounts, bank movements — and not a forecast history, a covenant, or a Dodo feed. So the
toolset derives what those rows can legitimately support and refuses what they cannot:

* **Derived** — a direct-method 13-week forecast (open receivables at their due date
  weighted by the aging band's collection probability, open payables in full, opening cash
  from settled bank movements), the aging summary, ranked collection and deferral
  candidates, a supplier profile, the policy thresholds.
* **Refused, with the reason** — no variance bridge without a prior published version, no
  measured error percentiles without forecast history, no covenant status without
  facilities, no Dodo breakdown without Dodo. Each raises `ToolError`, the agent records
  `degraded`, and the screen names the agent and what it was missing.

The modelling choices that are not in a tenant's data — a collection curve, a deferral
window — live in `config/tenant_terms.yaml`, versioned, and every derived figure carries
the band it came from in its own basis string. The liquidity floor is per source and set
on the Forecast screen: NovaTech's $15M floor is NovaTech's, and holding a freight
company's $12M book to it would put someone else's number on the screen.

## Agent space

`/war-room` opens on the agent console, and `/agents` is the same console with the roster
foregrounded. Both show the six specialists — job, tool allowlist, routed model,
**dependencies** — each with a **Start agent** button, above a graph of who feeds whom.

The dependencies are real, not decoration. Supplier Risk exists to challenge AP
Optimisation's proposals, so its card says so and its edge is drawn dashed until AP has
actually produced some; run AP first and Supplier Risk argues with its live proposals
(and, on the demo ledger, rejects two of them). Nothing is greyed out: an agent that can
only answer half the question answers half of it and reports what it was missing, which is
more useful than a disabled button.

A hand-started run goes through `AgentRunner` like any other: same allowlist, same context
budget, same evidence validator, same `AgentRun` on the same event bus, so it shows up in
the War Room stream alongside the Commander's wave and its findings are exactly as
citable. Citations use the tenant's own document key — `ar_ledger:AR-4099` — so the
Evidence Explorer shows the invoice number that is on the invoice.

## Layout

```text
backend/finance/          Money, FX, policy, provenance, forecast engine, controls
backend/contracts/        shared Pydantic contracts (agents + engine DTOs)
backend/tools/            tool protocol, FixtureToolset, EngineToolset, TenantToolset
backend/models/           SQLModel entities
backend/seed/             NovaTech generator + shocks
backend/integrations/     Dodo adapter (webhooks, payout lag, metrics)
backend/ingest/           DB Agent: introspect, classify, map, reconcile, load, drift
backend/agents/           specialist agents and LLM providers
backend/orchestrator/     weekly cycle, investigation, stress, replan
backend/api/              FastAPI data source / cycle / SSE / recommendation / approvals
frontend/                 Next.js Data Source / Forecast / Agents / War Room / Recommendation / Evidence UI
config/                   TreasuryPolicy, tenant terms, model routing
tests/                    finance, seed, tools, agents, ingest, integrations, e2e
docs/                     workflow, phases, schema adaptation
person1.md / person2.md   ownership plans
```

Read `docs/WORKFLOW.md` then `docs/PHASES.md` before changing contracts.
