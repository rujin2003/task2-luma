# WAR ROOM — The Workflow

**The product is the weekly 13-week cash flow forecast cycle.** The war room is what
happens when that cycle detects a policy breach. Not the other way round.

This document is the domain grounding: who does this work, on what cadence, with what
artifacts, and where judgement actually enters.

---

## 1. Why this workflow

The 13-week rolling direct-method cash forecast is the single most standard recurring
deliverable in corporate treasury. At a company of NovaTech's size it consumes **one to two
days of a treasury analyst's week, every week**, and it is almost always assembled in Excel
from:

- 4-8 bank portals (yesterday's closing balances, by account, by currency)
- an AR aging exported from the ERP, already a day or two stale
- an AP open-payables list plus the pending payment run
- the payroll calendar
- the tax calendar
- the debt service schedule
- a folder of one-off commitments living in someone's head

Then it is re-keyed, re-bucketed, and reconciled against last week's version by hand.

That is the pain point. It is weekly, it is skilled, it is repetitive, and it is
error-prone in ways nobody catches until the variance shows up.

A liquidity crisis, by contrast, happens once every few years. Building for the crisis and
ignoring the Monday cycle produces a product that is impressive in a demo and unused on a
normal Tuesday.

---

## 2. Roles

The spec's original claim that "the primary user is a CFO" is not how this works. CFOs
consume outputs; they do not operate systems.

| Role | Relationship to the product |
|---|---|
| **Treasury Analyst / Cash Manager** | Operates it. Lives in the forecast screen. **This is the user.** |
| **Assistant Treasurer / Treasurer** | Reviews the variance, challenges assumptions, signs off before publication |
| **Controller** | Owns the bank reconciliation and the cut-off; consumes the cash actuals |
| **CFO** | Receives the published forecast and the escalation recommendation; approves consequential actions |
| **AP Manager / Procurement** | Executes deferrals; is the counterparty to the Supplier Risk challenge |
| **Collections / AR** | Executes the collection worklist |

RACI for the weekly cycle: Analyst **R**, Treasurer **A**, Controller **C**, CFO **I** —
inverting to CFO **A** only when an escalation produces an action above their approval
threshold.

---

## 3. The forecast structure

**Direct method.** Actual cash in and out by category — not net income adjusted for
non-cash items. Indirect-method forecasting is a planning tool; treasury runs direct.

**13 weeks, rolling.** Each cycle drops week 1 and adds week 14.

### Categories

Fixed line items, because a forecast whose categories move cannot be variance-analysed
against its own history.

**Inflows**
| Category | Driver | Cadence |
|---|---|---|
| Customer receipts — trade AR | aging bucket × historical collection curve | continuous, weekday-weighted |
| Subscription / recurring receipts | Dodo billing schedule × success rate | monthly/annual anniversary dates |
| Other receipts | tax refunds, asset sales, interest | lumpy, dated |

**Outflows**
| Category | Driver | Cadence — *this is where realism lives* |
|---|---|---|
| Payroll — salaried | headcount × rate | semi-monthly (15th, last business day) or biweekly |
| Payroll — hourly/contract | timesheets | biweekly, one week in arrears |
| Payroll taxes & benefits | % of gross | follows payroll; deposit schedule is semi-weekly or monthly by lookback |
| AP — trade vendors | approved payment run | weekly run (e.g. Thursday) or 1st & 15th |
| Rent & leases | contracts | 1st of month |
| Income tax — estimated | prior-year safe harbour | quarterly: 15 Apr / 15 Jun / 15 Sep / 15 Dec |
| Sales / VAT remittance | taxable sales | monthly or quarterly by jurisdiction |
| Debt service — interest | facility rate × drawn | monthly or quarterly on facility anniversary |
| Debt service — principal | amortisation schedule | fixed dates |
| Capex | committed POs | milestone-dated |
| Insurance & annual software | renewal calendar | annual, single large dates |

**Net**
```
Opening cash → net change → closing cash
+ undrawn revolver capacity = available liquidity
vs minimum cash policy and covenant floor
```

### The cadence details that separate real from fake

- **Biweekly payroll produces three payrolls in some months.** Twice a year. It is the
  single most common cash surprise at companies that forecast payroll as
  "monthly ÷ 4.33". Model pay dates, never monthly averages.
- **Payroll tax deposits are not payroll.** They follow on a separate IRS deposit schedule
  determined by a lookback period. Separate line, separate date.
- **AP payment runs are batch events on fixed days**, not a smooth outflow. The 13-week
  grid should show a spike every Thursday, not a flat line.
- **Weekday weighting.** Customer receipts do not arrive on weekends, and lockbox/ACH
  posting lags differ. A forecast that spreads collections evenly across 7 days is wrong
  in week 1, which is the week that matters most.
- **Bank holidays shift settlement.** A payment dated Friday before a Monday holiday
  settles Tuesday.

---

## 4. The Monday cycle

What is automated, what is human, and where judgement enters. This is the product.

| Step | Who | What |
|---|---|---|
| 1. Refresh actuals | **Automated** | Pull prior-week bank transactions and closing balances; reconcile to GL cash; classify to categories |
| 2. Build variance bridge | **Automated** | Last week's forecast for last week vs actual, decomposed by category |
| 3. Explain the variance | **Agent** | Root-cause each material delta with evidence to source rows |
| 4. Refresh drivers | **Automated** | New AR aging, new AP open items, Dodo events, updated calendars |
| 5. Reforecast weeks 1-13 | **Automated** | Deterministic engine; each line carries source, assumption, method |
| 6. Update accuracy | **Automated** | Roll forward MAPE by category and horizon |
| 7. Surface exceptions | **Agent** | Only what moved materially, or where an assumption went stale |
| 8. **Review & challenge** | **Analyst → Treasurer** | *The judgement step.* Accept, override with reason, or request investigation |
| 9. Publish | **Treasurer** | Locks the version. It becomes next week's comparison baseline |
| 10. Policy check | **Automated** | Minimum cash, 30-day liquidity, covenant headroom → escalate if breached |

Steps 1-7 and 10 collapse a day of spreadsheet work into minutes. Steps 8-9 are
deliberately human, and the override is a first-class recorded object — an overridden
assumption with a stated reason is *better* data than a model-generated one, and it feeds
back into accuracy tracking.

---

## 5. The variance bridge

The single most-used artifact in the whole cycle. What the Treasurer opens first.

```
Week ending 2026-08-30          Forecast   Actual   Variance   Explanation
──────────────────────────────────────────────────────────────────────────
Opening cash                       21.4     21.4        —
Customer receipts — trade           4.2      3.1      (1.1)    Customer A inv 1832
                                                               $900K slipped to W2;
                                                               promise-to-pay 09-04
Subscription receipts (Dodo)        0.7      0.5      (0.2)    Success rate 94.1%→89.3%;
                                                               41 soft declines, retryable
Payroll — salaried                 (2.1)    (2.1)       —
Payroll taxes                      (0.6)    (0.6)       —
AP — trade run                     (1.8)    (2.4)     (0.6)    3 invoices pulled forward
                                                               at vendor request
Rent                               (0.3)    (0.3)       —
──────────────────────────────────────────────────────────────────────────
Closing cash                       21.5     19.6      (1.9)
```

Two bridges are produced each cycle:

1. **Forecast vs actual** for the week just closed — did we forecast correctly?
2. **Forecast vs prior forecast** for weeks 1-12 — what changed in our view of the future,
   and why?

The second is what answers the spec's original question *"what caused the cash forecast to
change?"* — but as a routine weekly artifact rather than a crisis investigation.

**Materiality gate.** Only variances above threshold get an agent-generated explanation.
Everything else is bucketed as immaterial. Finance people think in materiality; explaining
a $4K variance signals you don't.

---

## 6. Forecast accuracy — this replaces LLM confidence

A model reporting `"confidence": 0.87` is unfalsifiable and a finance professional will
distrust it on sight. Confidence in treasury is empirical: measured error, by category, by
horizon.

```
Mean absolute percentage error, trailing 26 weeks

Category                    W1      W4      W8     W13    n
────────────────────────────────────────────────────────────
Payroll — salaried         0.0%    0.0%    0.2%   0.4%   26   contractual
Rent & leases              0.0%    0.0%    0.0%   0.0%   26   contractual
Debt service               0.0%    0.1%    0.1%   0.3%   26   scheduled
Payroll taxes              0.3%    0.4%    0.8%   1.1%   26
AP — trade                 5.2%    9.1%   16.4%  24.8%   26
Dodo subscriptions         2.4%    5.5%   11.2%  17.9%   26
Customer receipts          3.1%    7.8%   14.3%  22.1%   26   dominant error source
────────────────────────────────────────────────────────────
Total net change           2.8%    6.4%   12.1%  19.6%
```

Three things this buys you:

- **Credibility.** "Our week-4 customer receipts forecast has been within 7.8% over 26
  weeks" is a claim a CFO can act on. `0.87` is not.
- **Honest stress calibration.** The stress-test scenarios stop being arbitrary. "AR −10%"
  becomes "AR at the 90th percentile of our own observed error", which is defensible.
- **Attention routing.** Categories with near-zero error need no review. Analyst time goes
  where the error is.

Requires storing forecasts bitemporally — which Phase 1 already does. **The seed dataset
must therefore include ~26 weeks of prior forecast versions**, not just a current state,
or there is nothing to backtest.

---

## 7. Escalation — where the war room comes in

The policy check runs every cycle. When it breaches, the war room opens:

```
Policy check fails
   projected minimum cash W6 = 18.2M vs floor 20.0M
        │
        ▼
War room opens on a specific, dated, quantified breach
        │
   Commander decomposes the investigation
        │
   Agents investigate in parallel → findings with evidence
        │
   AP proposes deferral ⟷ Supplier Risk rejects it
        │
   Follow-up investigation resolves by evidence
        │
   Candidate action bundles generated
        │
   Constraint gate → stress test → one fails → replan
        │
   Worklist produced, routed by delegation of authority
        │
   Approved actions execute; forecast reflects them next cycle
```

Everything agentic and impressive survives. It is now *triggered by* the routine workflow
rather than substituting for it — and the breach is specific and dated, not vibes.

---

## 8. Output is a worklist, not a strategy

"Accelerate $2.6M AR" is not something anyone can approve or do. This is:

```
#  Owner       Action                                    Counterparty      Amount   Due    Status
1  A. Rivera   Call re: overdue invoice 1832             Customer A        900K     09-08  open
2  A. Rivera   Offer 2/10 net 30 on invoices 1901,1904   Customer B        640K     09-09  open
3  Treasury    Retry 41 soft-declined Dodo payments      —                 310K     09-07  queued
4  M. Chen     Defer payment run items 44-51 by 14 days  Vendors (7)      (720K)    09-10  needs approval
5  Treasury    Draw revolver                             Lender            1.5M     09-11  needs CFO approval
```

Every row has an owner, a counterparty, a specific document reference, an amount, a date,
and a status. It closes the loop: next cycle's variance bridge shows which rows actually
landed, and that feeds collection-probability calibration.

**Excluded by hard constraint, shown explicitly:** payroll (protected), estimated tax
payment 09-15 (protected), Vendor Q (sole-source, 6-week replacement lead time — Supplier
Risk rejection, evidence attached).

Showing what was *rejected and why* is as important as showing what was recommended. It is
the first thing an experienced treasurer looks for.

---

## 9. Controls

Real finance systems have these. A demo without them reads as untested.

**Delegation of authority.** Not a binary `requires_approval`:

```
Action                          Analyst  Treasurer  CFO   Board
Collection call / dunning          A
Early-pay discount ≤ 2%            R         A
Early-pay discount > 2%            R         R        A
AP deferral < $250K                R         A
AP deferral $250K – $1M            R         R        A
AP deferral > $1M                  R         R        R      I
Revolver draw ≤ $2M                R         R        A
Revolver draw > $2M                R         R        R      A
Anything touching payroll or tax        BLOCKED — policy constraint
```

**Segregation of duties / maker-checker.** Preparer ≠ reviewer ≠ approver, enforced in
code, not convention. The analyst who prepares the worklist cannot approve it. SOX-relevant
and non-negotiable in any company large enough to be audited.

**Materiality thresholds.** Configurable, typically the lesser of a % of monthly operating
expense or a fixed floor. Drives which variances get explained, which exceptions surface,
and which items need review at all.

**Period and cut-off awareness.** Accountants work in periods. The system must know the
close calendar, respect cut-off dates, and never silently post across a closed period.

**Audit trail.** Every forecast version, every override with its stated reason, every
approval with who/when/what-they-saw/which-data-snapshot. Immutable.

---

## 10. The bank reconciliation artifact

This is the controllership-facing output, and it is where the Cash Position Agent's work
should surface — as a real recon, not a "discrepancy detected" toast:

```
Bank reconciliation — Operating account ...4471 — as of 2026-08-30

Balance per bank statement                              12,418,022
  Less: outstanding cheques (7)                           (184,300)
  Add:  deposits in transit (3)                            271,900
  Less: unrecorded bank fees                                 (2,140)
Adjusted bank balance                                    12,503,482

Balance per general ledger                               12,506,922
  Less: duplicate journal entry JE-88214                    (3,440)
Adjusted book balance                                    12,503,482

Difference                                                        0   ✓

Aged unreconciled items: 2 items > 30 days — flagged for Controller
Prepared: system  ·  Reviewed: ______  ·  Approved: ______
```

Book balance, bank balance, reconciling items, aged exceptions, and a sign-off block. That
is what an accountant expects to see, and producing it correctly is a strong signal that
the system understands the domain rather than simulating it.
