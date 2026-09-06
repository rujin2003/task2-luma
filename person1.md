# Person 1 — Financial Spine

**You own:** the data model, the seeded dataset, the deterministic forecast engine, the
Dodo integration, tenant onboarding, and backend enforcement of controls.

**Your north star:** every number the product shows comes from your code, is unit-tested,
and reconciles. Person 2's agents can only *call* your engine — they can never compute.
If your layer is right, their layer cannot produce a wrong number.

**Read first:** `WORKFLOW.md` (defines the product), then `PHASES.md`.

---

## File ownership

**Yours — edit freely:**
```
backend/models/**        backend/finance/**       backend/seed/**
backend/integrations/**  backend/ingest/**        alembic/**
tests/finance/**         tests/seed/**            calibration/**
```

**Shared — change only by agreement with Person 2, both review the PR:**
```
backend/contracts/**     # Pydantic schemas
backend/tools/**         # tool signatures (you write the bodies, they call them)
config/**
```

**Theirs — do not edit:** `backend/agents/**`, `backend/orchestrator/**`,
`backend/api/**`, `frontend/**`

Branch naming: `p1/<phase>-<topic>`. Small PRs. Never push to `dev` directly.

---

## Stage 0 — Phase 0: Foundation (with Person 2, day 1)

Do this **together, in one sitting.** Everything after depends on getting it right, and
this is the last time you both touch the same files heavily.

Your half:
- `Money` value type — integer minor units + ISO-4217 currency, always paired.
  Property tests for add/subtract/allocate/convert with no rounding leakage.
- CI rule that fails the build on `float` anywhere in `backend/finance/**`.
- `Provenance` primitive: `(source_system, source_table, source_pk, field, as_of, retrieved_at)`.
- Repo layout, Alembic, pytest, ruff, mypy strict on `finance/`.
- `TreasuryPolicy` config model — min cash, min 30-day liquidity, max revolver utilization,
  protected payment classes, materiality thresholds. Versioned row, never hard-coded.

**Agreed jointly and then frozen:** the Pydantic contracts in `backend/contracts/` and the
tool signatures in `backend/tools/`. Write the signatures with `NotImplementedError` bodies.
Person 2 codes against these immediately; you fill them in.

**Done when:** `Money` passes property tests, `TreasuryPolicy` loads and validates, CI is
green, and `backend/tools/` has every signature stubbed.

---

## Stage 1 — Phases 1 & 2: Model and Data

### Phase 1 — Financial Data Model

Entities (revised — no `Subsidiary`, `FXPosition`, `FXHedge` in v1):
`Company`, `BankAccount`, `BankTransaction`, `BankReconciliation`, `GLAccount`,
`GLTransaction`, `Customer`, `Invoice`, `Payment`, `Vendor`, `VendorInvoice`,
`PaymentRun`, `DebtFacility`, `DebtCovenant`, `Subscription`, `FinancialEvent`,
`Scenario`, `StressTest`, `Recommendation`, `Approval`, `AgentRun`, `Evidence`.

New entities the re-anchor requires — **these are the ones that matter most**:
`ForecastVersion`, `ForecastLine`, `VarianceItem`, `AccuracyStat`, `Override`,
`WorklistItem`, `ApprovalRoute`, `CalendarEvent`, `AccountingPeriod`.

Rules:
- Money = `BIGINT` minor units + `CHAR(3)` currency, always paired.
- Bitemporal `effective_at` / `recorded_at` on anything an agent will cite.
- `is_synthetic` and `tenant_id` on every fact table; Postgres RLS.
- Append-only `FinancialEvent`; entity tables are projections.

**Done when:** a 3-invoice company seeds and bank balance, AR balance and GL cash reconcile
via SQL alone.

### Phase 2 — Seeding

- Calibration layer: fit parameters offline from the sources in `DATA_SOURCES.md`
  (SEC XBRL peer ratios, Frankfurter/Treasury rates, IBM/Olist payment-delay mixtures).
  Commit **fitted parameters** to `calibration/novatech_profile.json`, never dataset rows —
  smaller, licence-clean, deterministic.
- Deterministic generator: one seed produces a byte-identical dataset.
- **26 weeks of prior `ForecastVersion` rows.** Non-negotiable. Without forecast history
  there is nothing to backtest and Person 2's accuracy panel has no data. This is the most
  commonly missed seeding requirement — do it now, not in Phase 3.
- Real cadences per `WORKFLOW.md` §3: dated pay runs including the two months with three
  biweekly payrolls, separate payroll-tax deposit dates, Thursday AP runs, 1st-of-month
  rent, quarterly estimated taxes, weekday-weighted receipts, bank-holiday shifts.
- Consistency invariants enforced at generation and re-checked in tests.
- Named, replayable anomaly pack: `customer_delay_4m`, `dodo_success_rate_drop`,
  `supplier_acceleration_1m`, `bank_gl_variance`, `covenant_headroom_squeeze`,
  `stale_forecast_assumption`.

### ⚠ Your Stage 1 deliverable to Person 2 — do this EARLY

**Hand-written fixture JSON for every tool signature**, committed to `tests/fixtures/`,
matching the frozen contracts. Person 2 is blocked without these. Ship them in the first
few days, before the real generator works. They can be crude — they just have to be
schema-valid and internally plausible.

**Done when:** `make seed SEED=42` twice gives identical dumps, all invariants pass, the
shock pack applies and rolls back, and fixtures are in Person 2's hands.

---

## Stage 2 — Phase 3: Forecast Engine (your crown jewel)

This is the largest and most important thing you build.

- `cadence.py` — the calendar layer. Pay-date generation (semi-monthly and biweekly),
  payroll-tax deposit schedules, AP run days, quarterly estimated tax dates, debt service
  dates, weekday weighting, bank holidays and settlement shifts. **100% branch coverage.**
- `forecast.py` — 13-week rolling direct-method forecast, weekly buckets, fixed category
  set. Every line carries source, assumption, method, as-of. Rolling: drop week 1, add 14.
- `variance.py` — forecast vs actual for the closed week, and forecast vs prior forecast
  for weeks 1-12, decomposed by category, materiality-gated.
- `accuracy.py` — MAPE by category × horizon over a trailing window. **This replaces
  LLM confidence throughout the product**, and calibrates Person 2's stress scenarios.
- `cash.py` — aggregation, restricted vs unrestricted, and the bank reconciliation
  artifact (`WORKFLOW.md` §10) with reconciling items, aged exceptions and sign-off block.
- `covenants.py` — with the real test date and definition, not a continuously evaluated
  ratio. **100% branch coverage.**
- `debt.py` — facility capacity, draw cost, amortisation.
- `constraints.py` — structured pass/fail with violated constraint and margin. Payroll and
  tax delays forced to zero via policy. **100% branch coverage.**
- `materiality.py` — threshold resolution.

**Done when:** a forecast reproduces a hand-worked 13-week grid exactly; the variance
bridge for a known week ties to zero; MAPE computes against the seeded 26 weeks.

---

## Stage 3 — Phase 4: Dodo + Merge Point 1

### Phase 4 — Dodo Payments

- `integrations/dodo/` — client, DTOs, normalizer, webhook receiver. Nothing outside this
  directory knows Dodo exists; the rest of the system sees `PaymentEvent`.
- Backfill (List Payments / Subscriptions / Refunds / Disputes / Payouts / Balance Ledger)
  and live webhooks with signature verification and idempotent replay.
- **Model the payment → balance-ledger → payout lag explicitly.** A successful payment is
  not cash. That lag is what moves the 30-day liquidity number, and most implementations
  get it wrong.
- Recoverability from Dodo's documented soft/hard decline taxonomy — cite the failure code
  as evidence, never invent a recovery percentage.
- Read-only API key for all ingestion paths.
- Graceful degradation: Dodo down → last-known forecast, reduced confidence, flagged.

### 🔀 Merge Point 1 — with Person 2

Replace their fixture tools with your real engine behind the same signatures. If you both
held the contract, this is a config swap and a day of fixing assumptions. If either of you
drifted, this is where you find out — so **do a joint dry run of one tool end-to-end in
Stage 2**, well before you need it.

---

## Stage 4 — Phases 2A & 10 (backend)

### Phase 2A — Schema Cartographer

Depends on Person 2's agent runtime (Phase 5), so it lands here. Full design in
`SCHEMA_ADAPTATION.md`.

- Stages 0-2 and 4-7 are yours and deterministic: connect (read-only), introspect to a
  `SchemaFingerprint`, template match, **reconciliation gate**, freeze to `mapping.vN.yaml`,
  drift watch.
- Stage 3's four sub-agents (Table Classifier, Column Mapper, CoA Roler, Unit & Currency
  Detective) run on Person 2's runtime — you supply the input profiles and consume the
  structured output. Coordinate the four schemas with them.
- The reconciliation gate reuses your Phase 2 invariant code. That's why this is yours.
- **No raw financial rows ever leave for a model** — column names, types and format
  signatures only.

### Phase 10 — Controls (backend half)

- Delegation-of-authority matrix — routing by action type and amount band.
- Segregation of duties / maker-checker — preparer ≠ reviewer ≠ approver, **enforced in
  code**, not convention.
- Materiality thresholds wired into variance, exceptions and review routing.
- Immutable audit log: who approved, when, what they saw, which data snapshot.
- Execution adapters behind an interface, dry-run by default.

**Done when:** a debt draw cannot execute without a recorded approval (test attempts it and
expects hard failure); a preparer cannot approve their own worklist; an action $10 over a
band routes one level higher.

---

## Stage 5 — Phase 11: Hardening (with Person 2)

Yours: determinism harness (same seed ⇒ identical numbers), failure injection for Dodo down
and incomplete bank data, golden-file assertions, `make demo` reset path.

---

## What Person 2 is doing (your dependencies)

| Stage | They build | You need from them | They need from you |
|---|---|---|---|
| 0 | Contracts, event schema | frozen contracts | frozen tool signatures |
| 1 | Agent runtime, UI scaffold | — | **fixture JSON (early!)** |
| 2 | 6 agents on fixtures | — | engine progress updates |
| 3 | Weekly cycle | — | **real engine behind tool signatures** |
| 4 | Commander, scenarios, UI | agent runtime for Cartographer | DoA matrix, audit log |
| 5 | Demo, hardening | — | determinism harness |

**Daily 15-minute sync.** The only two things that can genuinely hurt you: a contract change
neither of you noticed, and fixtures arriving late.
