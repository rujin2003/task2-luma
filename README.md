# WAR ROOM

Weekly 13-week rolling direct-method cash forecast, with a war room escalation when the
cycle detects a policy breach.

Merged Person 1 spine + Person 2 agents/frontend:

- `backend/finance/` — Money, cadence, forecast, variance, accuracy, cash, covenants, controls, audit, execution
- `backend/models/` + `alembic/` — SQLModel entities and migrations
- `backend/seed/` — deterministic NovaTech generator (`make seed SEED=42`) with 26-week forecast history
- `backend/tools/` — real `EngineToolset` behind the agent tool protocol
- `backend/integrations/dodo/` — client, webhooks (Standard Webhooks), payout lag, metrics, decline taxonomy
- `backend/ingest/` — Schema Cartographer (introspect → template match → reconcile → freeze → drift)
- `backend/agents/` + `backend/orchestrator/` — six specialists, weekly cycle, Commander path, stress/replan
- `backend/api/` + `frontend/` — Forecast, War Room, Recommendation, Evidence screens

## Setup

```bash
python3 -m pip install -e ".[dev]"
make ci
make seed SEED=42
make demo
```

```bash
# API (cycle, SSE, recommendation, approvals)
make serve

# UI (Forecast / War Room / Recommendation / Evidence)
cd frontend && npm install && npm run dev
```

`make demo` resets a SQLite DB, seeds NovaTech, applies the shock pack, and asserts the
golden spine narrative in `tests/fixtures/demo/`. It also runs the deterministic war-room
path (fixture tools + FakeProvider): breach → investigation → conflict → stress failure →
replan → recommendation.

`make ci` runs ruff, mypy (strict on `backend/finance`), the no-float gate, and pytest.

## Layout

```text
backend/finance/          Money, FX, policy, provenance, forecast engine, controls
backend/contracts/        shared Pydantic contracts (agents + engine DTOs)
backend/tools/            tool protocol, FixtureToolset, EngineToolset
backend/models/           SQLModel entities
backend/seed/             NovaTech generator + shocks
backend/integrations/     Dodo adapter (webhooks, payout lag, metrics)
backend/ingest/           Schema Cartographer + mapping freeze
backend/agents/           specialist agents and LLM providers
backend/orchestrator/     weekly cycle, investigation, stress, replan
backend/api/              FastAPI cycle / SSE / recommendation / approvals
frontend/                 Next.js Forecast / War Room / Recommendation / Evidence UI
config/                   TreasuryPolicy + model routing
tests/                    finance, seed, tools, agents, ingest, integrations, e2e
docs/                     workflow, phases, schema adaptation
person1.md / person2.md   ownership plans
```

Read `docs/WORKFLOW.md` then `docs/PHASES.md` before changing contracts.
