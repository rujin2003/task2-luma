# WAR ROOM — Onboarding & Schema Adaptation Engine

**Problem.** Every company has a different database. A startup should be able to connect
theirs and see a working Executive Overview in minutes, without us writing code for them.

**Non-goal.** Universal ERP ingestion. We are not building an iPaaS. We are building a
narrow, self-service path from *someone else's schema* into *our canonical schema*, with
an objective correctness gate.

**The core claim:** schema mapping is a **configuration** problem solved once per schema
*shape*, not per customer. The engine below is designed so that the marginal LLM cost of
onboarding tenant N approaches zero.

---

## 1. Architecture: the Schema Cartographer

A pipeline of seven stages. Six are deterministic. Exactly one uses a model, and only on
the residue the deterministic stages could not resolve.

```
 [0] Connect (read-only)
        |
 [1] Introspect  ---------> SchemaFingerprint (compact, no customer data)
        |
 [2] Template match  -----> known shape? apply stored mapping, skip [3] entirely
        |  (residue only)
 [3] Cartographer agent --> proposed mappings + confidence
        |
 [4] Validate (deterministic reconciliation)  <-- THE GATE
        |
 [5] Human confirm (low-confidence items only)
        |
 [6] Freeze -> mapping.vN.yaml  ==> runtime ETL is now pure deterministic code
        |
 [7] Drift watch -> schema hash changes -> re-run delta only
```

### Stage 0 — Connect

- Read-only credentials, enforced at the database role level. The engine never holds
  write capability against a customer database. This is non-negotiable for a financial
  product.
- Supported entry points, in order of how most startups will actually arrive:
  1. Postgres / MySQL read replica connection string
  2. Accounting API — QuickBooks, Xero
  3. Billing API — Stripe, Dodo Payments
  4. Bank files — camt.053 / BAI2 / MT940
  5. CSV upload (the universal fallback; also the demo path)
- Everything lands in a per-tenant `raw.*` schema, stored verbatim and never mutated.

### Stage 1 — Introspect (deterministic, free)

Produce a `SchemaFingerprint`: tables, columns, declared types, primary and foreign keys,
row counts, null rates, distinct-value cardinality, min/max for dates and numerics, and a
**format signature** per column (e.g. `^INV-\d{6}$`, `^[A-Z]{3}$`, `ISO8601`).

**Critical property: the fingerprint contains no customer financial data.** Column names,
types, and shapes only. This is simultaneously the privacy control and the token-budget
control — it is what makes Stage 3 cheap enough to run on a small model.

### Stage 2 — Template match (deterministic, free)

Match the fingerprint against a library of known schema shapes: QuickBooks, Xero, NetSuite,
Odoo, Stripe, Dodo, and the common Rails/Django/Prisma SaaS billing layouts.

Scoring is structural — table-name similarity, column-set overlap, FK topology, format
signatures. Above the match threshold, the stored mapping is applied wholesale.

**This is the scalability mechanism.** The tenth startup running Stripe + QuickBooks costs
zero model calls. Only genuinely novel schemas reach Stage 3, and each one that gets
confirmed is contributed back to the library — so the library gets better as you grow, and
per-tenant onboarding cost trends to zero.

Contributed templates store **column names, types, and role assignments only** — never
values, never customer identifiers.

### Stage 3 — Cartographer agent (the only model call)

Runs only on unmatched tables and columns. Decomposed into four narrow, single-purpose
sub-agents rather than one large reasoning task — small models are reliable at narrow
classification with a tight output schema and unreliable at sprawling open-ended mapping.

| Sub-agent | Input | Output | Typical context |
|---|---|---|---|
| **Table Classifier** | table name + column-name list + row count + FK edges | entity role (`invoice`, `customer`, `payment`, `vendor_invoice`, `bank_txn`, `gl_entry`, `ignore`) | < 1 KB |
| **Column Mapper** | one table's column profiles + target entity schema | column -> canonical field, per-field confidence | < 2 KB |
| **CoA Roler** | GL account number, name, normal balance, posting-volume profile | one of ~15 canonical account roles | < 1 KB |
| **Unit & Currency Detective** | numeric column profile + sample format signatures | minor vs major units, currency source (column / implied / per-tenant default) | < 1 KB |

Every sub-agent returns a strict JSON schema. Every one emits a confidence score. None of
them ever sees a raw financial row.

**The Unit & Currency Detective earns its place.** Storing amounts in major units when the
source used cents (or vice versa) is a silent 100x error that reconciliation catches only
if you check — and it is the single most common real-world ingestion defect.

### Stage 4 — Validate (deterministic — this is the gate)

A proposed mapping is accepted only if it reconciles. You never have to trust the model:

```
Σ mapped open invoices        == tenant-stated AR balance      (tolerance: 0)
Σ mapped open vendor invoices == tenant-stated AP balance      (tolerance: 0)
Σ mapped bank transactions    == tenant-stated cash balance    (tolerance: configurable)
Σ GL debits                   == Σ GL credits                  (tolerance: 0)
every invoice                 -> resolvable customer FK
every currency code           -> valid ISO-4217
every date                    -> within a plausible range, timezone-normalized
```

Plus a **coverage score**: percentage of source rows and source monetary value successfully
mapped. Unmapped value is reported explicitly — a mapping covering 92% of AR is a finding
the tenant must see, not something to silently round away.

A wrong mapping fails arithmetically. That property is what makes it safe to let a small,
cheap model propose mappings in the first place.

### Stage 5 — Human confirm

The reviewer sees only what failed or scored low — never the full mapping. Each item shows
the proposed target, the confidence, the evidence (column profile, format signature,
sample-shape), and the reconciliation delta it would cause.

Target: a typical startup confirms fewer than 20 items.

### Stage 6 — Freeze

Emit `tenants/<id>/mapping.v<N>.yaml`, versioned and diffable:

```yaml
version: 3
source: postgres/custom
confirmed_by: cfo@startup.com
confirmed_at: 2026-09-05T11:20:00Z
coverage: {rows: 0.998, value: 1.000}
entities:
  Invoice:
    from_table: billing_invoices
    fields:
      external_id:  {col: id}
      customer_ref: {col: org_id, fk: organizations.id}
      amount:       {col: amount_cents, units: minor}
      currency:     {const: USD, reason: no currency column; tenant is single-currency}
      due_date:     {col: due_at, tz: UTC}
      issued_date:  {col: created_at, tz: UTC}
    filters: [{col: status, not_in: [draft, void]}]
gl_roles:
  "1000": cash_operating
  "1010": cash_restricted
  "1200": ar_trade
  "2000": ap_trade
  "2100": accrued_payroll
```

**After freeze there is no model in the runtime path.** Ingestion is deterministic ETL:
same input, same output, replayable, auditable, and cheap. This mirrors the Phase 5
principle — the model interprets at configuration time, code executes at runtime.

### Stage 7 — Drift watch

Hash the schema fingerprint on every sync. On change, re-run Stages 1-5 for the delta only,
and hold ingestion if reconciliation breaks rather than importing silently corrupted data.
Schema drift in a customer's ERP is normal and continuous; treating it as an exception is
how ingestion pipelines rot.

---

## 2. Canonical account roles

The chart of accounts is the hard part. Do not map account *numbers* — map to roles. A
company with 4,000 GL accounts still has only these:

```
cash_operating        ar_trade            ap_trade           revolver_drawn
cash_restricted       ar_intercompany     accrued_payroll    term_debt
cash_mmf              ar_other            taxes_payable      interest_expense
                      deferred_revenue    accrued_expenses   fx_gain_loss
```

Roughly fifteen roles cover everything the treasury engine reads. Roles are what the
covenant and cash modules consume; account numbers never leave the mapping layer.

---

## 3. Capability manifest

Not every startup has FX, a revolver, subsidiaries, or a GL worth reading. Ingestion emits
a per-tenant manifest:

```yaml
capabilities:
  bank_feed: true
  gl: partial          # cash + AR/AP control accounts only
  ar_subledger: true
  ap_subledger: true
  fx: false            # single currency
  debt: false          # no facilities found
  dodo: true
  payroll_calendar: false
```

This feeds directly into the Commander's dynamic agent selection (Phase 7): a tenant with
`fx: false` causes the FX Agent to be **skipped with a stated reason**, not to run and
invent an exposure. Schema variability stops being a liability and becomes an input to the
planning behaviour the product already needs.

It also drives honest UI degradation — "cash runway unavailable: no payroll calendar
connected" beats a confidently wrong number.

---

## 4. Rules for turning foreign data into our schema

These apply to tenant ingestion and to the synthetic NovaTech dataset alike:

| Rule | Why |
|---|---|
| **Land raw, never mutate** | The raw table is the evidence a recommendation cites. Overwrite it and provenance dies. |
| **Import distributions, not rows; lags, not dates; ratios, not amounts** | Applies when calibrating demo/benchmark data from public datasets — foreign rows carry a foreign company's currency, era and scale. |
| **Currency is never inferred at runtime** | Inferred once at mapping time, frozen, validated. An implied currency that changes behaviour between syncs is unauditable. |
| **Minor units everywhere, immediately** | Convert at the mapping boundary. Never let major-unit values into the canonical layer. |
| **`is_synthetic` and `tenant_id` on every fact row** | Demo data must be queryably distinguishable; multi-tenancy must be enforceable by RLS. |
| **Every canonical row keeps `source_system`, `source_table`, `source_pk`** | This is the bottom of the Evidence Explorer's provenance tree. |

---

## 5. Scaling

**Onboarding cost.** Model spend is per novel *schema shape*, not per tenant. With template
matching and library contribution, cost per new tenant declines toward zero. Budget the
model spend as a one-time onboarding cost, not a running cost.

**Runtime ingestion.** No model in the path. Incremental sync via a watermark column
(`updated_at` / CDC where available), full reconciliation sweep nightly, webhook streaming
for Dodo and Stripe. Horizontally scalable; per-tenant workers with isolated failure.

**Multi-tenancy.** `tenant_id` on every table, Postgres row-level security, per-tenant
`TreasuryPolicy` and capability manifest, per-tenant encryption of connection credentials.

**Blast radius.** A broken mapping for one tenant must not stall others. Per-tenant sync
queues, per-tenant circuit breakers, and a held-ingestion state that surfaces in that
tenant's UI only.

**Onboarding SLO.** Connect to first Executive Overview: under 10 minutes for a
template-matched schema, under 60 minutes for a novel one including human confirmation.

---

## 6. Security & compliance

- Read-only DB roles; credentials encrypted at rest, per-tenant keys; connection via
  allowlist or tunnel.
- **No raw financial rows are ever sent to a model.** Only column names, types, and format
  signatures. State this explicitly in onboarding — it is a sales asset as much as a
  control, and for a treasury product it is likely a hard procurement requirement.
- Full audit log of every mapping proposal, confirmation, and version change.
- Right to delete: dropping a tenant drops `raw.*`, canonical rows, and mappings together.
