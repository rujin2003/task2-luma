# WAR ROOM — Real Data Sources & Useful Data Points

**Purpose:** replace hand-invented numbers with data grounded in real-world sources.

**Verification status:** every endpoint marked *(verified)* was probed live and returned
real data during research. Dataset links marked *(verified)* returned HTTP 200. Anything
unverified is labelled as such.

**The core idea.** You cannot download "a mid-market SaaS company's treasury ledger" —
that data is private and does not exist publicly. What you *can* do is assemble NovaTech
from real components:

| NovaTech element | Where the realism comes from |
|---|---|
| FX rates, volatility | ECB / Frankfurter — real daily rates |
| Interest rates, borrowing cost | US Treasury Fiscal Data, ECB yield curve — real curves |
| Customer payment behaviour (DSO, lateness) | Public invoice/AR datasets — real empirical distributions |
| Balance-sheet scale & ratios | SEC XBRL company facts — real companies of comparable size |
| Covenant thresholds & language | SEC 10-K / credit-agreement exhibits — real covenant text |
| Payment success/failure/refund/dispute | Dodo Payments test mode — real API, real event shapes |
| Bank transaction shapes | Plaid / Teller sandbox — real institution-formatted transactions |
| Vendor/supplier concentration | Public procurement spend data — real supplier tail distributions |

Result: nothing is fabricated. Amounts are *scaled*, but distributions, timings,
rates and ratios are observed. That is defensible, and it directly satisfies the spec's
"do not use fake random numbers without financial relationships".

---

## 1. FX — Rates, Volatility, Exposure

### Frankfurter API *(verified)*
- `https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR,GBP,INR`
- `https://api.frankfurter.dev/v1/2026-07-01..2026-08-05?base=USD&symbols=EUR`
- No API key, no quota. ECB reference rates, history back decades.
- Live check returned: `{"amount":1.0,"base":"USD","date":"2026-09-04","rates":{"EUR":0.86044,"GBP":0.7391,"INR":94.49}}`

### ECB Data Portal API *(verified)*
- `https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A?lastNObservations=1&format=jsondata`
- Also serves the euro area yield curve (`YC` dataflow) — useful for EUR-denominated
  facility pricing.

### US Treasury Fiscal Data — Rates of Exchange *(verified)*
- `https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/rates_of_exchange`
- Quarterly official government translation rates. Useful specifically because a real
  treasury team distinguishes *spot* from *reporting/translation* rate — modelling both
  is a credibility detail most demos skip. Send a normal browser `User-Agent`.

**Use for:** `FXPosition`, `FXHedge`, FX shock stressors calibrated to real historical
moves (e.g. the actual worst 30-day EUR/USD drawdown in the last 5 years), and the
spec's `eur_obligation_increase` anomaly.

**Useful data points:** spot rate, 30/60/90d forward-equivalent, realized volatility,
max historical 30-day adverse move (for the FX shock stressor), rate as-of timestamp
(a stale rate is itself a finding the FX Agent can raise).

---

## 2. Interest Rates, Debt Facilities & Borrowing Cost

### US Treasury Fiscal Data — Average Interest Rates *(verified)*
- `https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/avg_interest_rates?page[size]=100`
- Live check returned real records back to 2001.

### Daily Treasury Yield Curve *(verified, HTTP 200 CSV)*
- `https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/2026/all?type=daily_treasury_yield_curve&_format=csv`
- Gives a real risk-free curve to price revolver draws and term debt.

### FRED (St. Louis Fed) — free API key required
- `https://api.stlouisfed.org/fred/series/observations?series_id=SOFR&api_key=...&file_type=json`
- Live check returned HTTP 400 without a key, i.e. the endpoint is alive and key-gated.
- Series worth pulling: `SOFR` (revolver base rate), `DGS10`, `BAMLC0A0CM`
  (IG corporate OAS -> credit spread), `DRTSCILM` (bank lending standards),
  `TOTBKCR`. **Not verified series-by-series** — confirm each ID before wiring it in.

### World Bank Indicators API *(verified, no key)*
- `https://api.worldbank.org/v2/country/US/indicator/FR.INR.LEND?format=json`
- Useful for lending rates in NovaTech's non-US subsidiary jurisdictions.

**Use for:** `DebtFacility` pricing (SOFR + spread), draw-cost estimation by the Debt
Agent, and the "higher financing cost" stressor calibrated to a real historical rate move
rather than an arbitrary +200bp.

**Useful data points:** base rate, credit spread by rating, commitment fee, undrawn
capacity, all-in draw cost, maturity ladder, days to next rate reset.

---

## 3. Covenants & Balance-Sheet Realism

### SEC EDGAR XBRL APIs *(verified — no key, requires a descriptive User-Agent)*
- Frames (one metric, all filers, one period):
  `https://data.sec.gov/api/xbrl/frames/us-gaap/AccountsReceivableNetCurrent/USD/CY2024Q4I.json`
  — live check returned 3,263 real filer data points.
- Company facts (all metrics, one filer):
  `https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json` *(verified)*
- Set `User-Agent: <app> <email>` and stay under ~10 req/s.

**This is the single highest-value source for the project.** Pull the XBRL frames for a
peer set of ~$400-500M-revenue companies and you get *real* distributions for:

| Concept | XBRL tag |
|---|---|
| Revenue | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Cash | `CashAndCashEquivalentsAtCarryingValue` |
| Restricted cash | `RestrictedCashAndCashEquivalentsAtCarryingValue` |
| AR | `AccountsReceivableNetCurrent` |
| AP | `AccountsPayableCurrent` |
| Long-term debt | `LongTermDebtNoncurrent` |
| Revolver drawn | `LineOfCreditFacilityAmountOutstanding` |
| Revolver capacity | `LineOfCreditFacilityMaximumBorrowingCapacity` |
| Operating cash flow | `NetCashProvidedByUsedInOperatingActivities` |

From these you derive real DSO, DPO, cash-to-revenue ratio, revolver utilization and
leverage for companies of NovaTech's size — so NovaTech's opening balance sheet is a
*plausible real company*, not a round number.

### Credit agreements (covenant language)
- EDGAR full-text search: `https://efts.sec.gov/LATEST/search-index?q=%22consolidated+leverage+ratio%22&forms=10-K`
  *(endpoint not probed — verify before use; the UI at `https://www.sec.gov/edgar/search/` works)*
- Ex-10 credit agreement exhibits contain real covenant definitions: Consolidated
  Leverage Ratio, Interest Coverage, minimum liquidity, springing covenants, cure rights.
- **Insight:** real credit agreements define Net Debt/EBITDA on *trailing-twelve-month
  adjusted* EBITDA with specific add-backs, and often test only *quarterly*. A system that
  tests the covenant continuously and on unadjusted EBITDA will look wrong to any CFO.
  Model the test date and the definition, not just the ratio.

**Useful data points:** covenant type, threshold, test frequency, test date, definition
reference, current value, headroom in currency and in %, days to next test, cure rights.

---

## 4. AR — Invoices, Aging & Payment Behaviour

The goal here is *not* to import invoices. It is to import **empirical payment-delay
distributions per customer segment**, then generate NovaTech invoices that obey them.

| Dataset | Status | What it gives you |
|---|---|---|
| [Finance Factoring — IBM Late Payment Histories](https://www.kaggle.com/datasets/hhenry/finance-factoring-ibm-late-payment-histories) | *(verified 200)* | Invoice-level AR with due date vs paid date, disputes. The closest public analogue to a real AR ledger. |
| [Payment date prediction / customer invoices](https://www.kaggle.com/datasets/pradumn203/payment-date-prediction-for-invoices-dataset) | *(verified 200)* | Open-invoice payment-date prediction set; per-customer payment-lag history. |
| [Olist Brazilian E-Commerce](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) | *(verified 200)* | ~100k real orders with order->payment->delivery timestamps, installments, multi-currency-adjacent behaviour. Excellent for realistic collection-lag curves. |
| [UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii) | *(verified 200)* | ~1M real invoice lines, 2009-2011, with returns/cancellations. Good for revenue seasonality and refund rates. |
| [UCI Default of Credit Card Clients](https://archive.ics.uci.edu/dataset/350/default+of+credit+card+clients) | *(verified 200)* | 30k real payment-history records; realistic delinquency-transition matrices for customer default risk. |

**Insight that matters for the AR Agent:** real invoice payment delay is strongly
**bimodal**, not normal — most invoices pay within a few days of terms, a long tail runs
30-90+ days late. If you generate lateness from a normal distribution, the AR Agent's
"realistic accelerated cash" number will be systematically wrong and the demo's central
claim collapses. Fit a two-component mixture per customer segment from the datasets above.

**Useful data points:** invoice amount/currency/issue date/due date/terms, days-past-due
bucket (current/1-30/31-60/61-90/90+), customer historical mean and variance of days-late,
early-payment-discount take-up rate, dispute flag and dispute age, promise-to-pay date,
customer concentration (% of AR in top 5), collection probability by bucket,
probability-weighted accelerable cash.

---

## 5. AP, Vendors & Supplier Risk

| Source | Status | Use |
|---|---|---|
| USAspending API — `https://api.usaspending.gov/api/v2/` | *(not probed — public, no key)* | Real vendor names, contract values, payment schedules, supplier concentration curves |
| UK Contracts Finder / gov spend over £25k | *(not probed)* | Real supplier spend tails |
| SEC 10-K "Concentration of suppliers" disclosures | via EDGAR *(verified)* | Real single-source-supplier language |
| [Olist sellers tables](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) | *(verified 200)* | Real seller/vendor distribution shapes |

**Insight:** supplier spend follows a steep Pareto — roughly 80% of spend sits with ~20%
of vendors, and *criticality does not correlate with spend*. The most dangerous supplier
to delay is often small. Build the dataset so that at least one low-spend, high-criticality
vendor exists; that is what gives the Supplier Risk Agent a genuine, non-theatrical reason
to overrule the AP Agent, which the spec explicitly calls for.

**Useful data points:** vendor invoice amount/due date/terms, early-pay discount
(e.g. 2/10 net 30 — a real, quantifiable cost of deferral), contractual late penalty,
vendor criticality tier, single-source flag, replacement lead time, historical disruption
incidents, spend concentration, protected-class flag (payroll/tax/statutory).

---

## 6. Dodo Payments *(verified against current docs)*

- Environments: test `https://test.dodopayments.com`, live `https://live.dodopayments.com`
- Auth: `Authorization: Bearer <API_KEY>`; read-only keys are available — **use a
  read-only key for all ingestion paths.**
- Official SDKs: TypeScript, Python, Go, PHP, Java, Kotlin, C#, Ruby, Rust.

Endpoints relevant to treasury (all confirmed present in the current API reference):

| Area | Endpoints |
|---|---|
| Payments | List Payments, Get Payment Detail, Get Invoice, Retrieve Line Items |
| Subscriptions | List/Get, Usage History, Credit Usage, Change Plan, Update Payment Method |
| Refunds | List Refunds, Get Refund Detail |
| Disputes | List Disputes, Get Dispute Detail |
| Payouts | List Payouts, Retrieve Payout Breakup, List Breakup Details, Download Breakup CSV |
| Balance | List Balance Ledger Entries (filter by date, event type, currency) |
| Webhooks | Create/List/Update/Delete, Get Signing Key, Get/Update Headers |
| Usage events | Ingest / Get / List |

Also documented and directly useful: **Error Codes** and **Transaction Failures**
(every failure code classified soft vs hard decline with a recommended recovery action),
plus a "Handle Payment Failures" guide.

**Insight #1 — the recoverability model comes free.** The spec asks the Dodo Agent to
estimate "recoverable failed payments". Dodo's own soft-vs-hard decline taxonomy is
exactly that model, published and authoritative. Soft declines (insufficient funds, issuer
timeout) are retry-recoverable; hard declines (stolen card, closed account) are not. Do not
invent a recovery percentage — derive it from the documented failure code, and cite the
code as evidence.

**Insight #2 — payouts, not payments, are cash.** `Payouts` + `Balance Ledger` are what
actually hit the bank account. The gap between "payment succeeded" and "payout settled" is
real working capital and belongs in the 30-day forecast. Most implementations treat a
successful payment as immediate cash and are simply wrong.

**Insight #3 — disputes are a cash reserve.** Open disputes are contingent outflows with
a resolution lag. Model a dispute reserve; the Covenant Agent should see it.

**Useful data points:** payment id/amount/currency/status/created_at, failure code and
soft/hard classification, refund amount and reason, dispute amount/stage/deadline, payout
id/amount/expected settlement date, balance-ledger entry type, subscription MRR/status/
next billing date/churn signal, rolling 7d and 30d success rate, success-rate delta vs
baseline (the trigger for the `dodo_success_rate_drop` anomaly).

---

## 7. Bank Accounts & Transactions

| Source | Status | Use |
|---|---|---|
| Plaid Sandbox — `https://plaid.com/docs/sandbox/` | *(verified 200)* | Free sandbox, real API shapes, `/transactions/sync`, pending vs posted, balances. Custom sandbox users let you script an exact transaction set. |
| Teller sandbox — `https://teller.io/docs` | *(verified 200)* | Alternative, simpler onboarding |
| GoCardless Bank Account Data (ex-Nordigen) | *(302 redirect; confirm current URL)* | Free-tier real EU/UK open-banking access |

**Insight:** Plaid's sandbox gives you the *pending vs posted* distinction and the
`available` vs `current` balance split for free. That is precisely the mechanism behind
the spec's required bank/GL discrepancy and "pending cash movements" — you get a realistic
reconciliation problem without inventing one.

**Useful data points:** account id/type/currency, current vs available balance, restricted
flag, transaction amount/date/pending flag/counterparty/category, value date vs booking
date, unreconciled items and their age, bank-vs-GL variance with classification (timing /
missing entry / duplicate / FX revaluation).

---

## 8. Macro & Sector Context (optional, low priority)

- World Bank Indicators *(verified)* — GDP, inflation, lending rates for subsidiary
  jurisdictions
- ECB Data Portal *(verified)* — euro-area rates and yield curves
- BLS / Eurostat — wage inflation for payroll forecasting *(not probed)*

Useful mainly for justifying forecast assumptions ("payroll grows 3.4% based on published
wage inflation") rather than driving core logic. Do not let this expand — it is garnish.

---

## Recommended Minimum Stack

If you only wire up four sources, use these — they cover the spec's demands with the least
integration effort:

1. **SEC XBRL frames** — real balance-sheet scale, ratios and covenant realism *(no key)*
2. **Frankfurter** — real FX rates and volatility *(no key)*
3. **Treasury Fiscal Data + FRED** — real rate curves and borrowing cost *(one free key)*
4. **Dodo test mode** — real payment/refund/dispute/payout events *(your own key)*

Add the **IBM Late Payment Histories** dataset for AR behaviour and **Plaid sandbox** for
bank transactions, and every number in the demo traces to something real.

---

## Attribution & Compliance Notes

- SEC EDGAR: public domain; a descriptive `User-Agent` with contact email is required and
  the fair-access rate limit applies.
- ECB / Frankfurter: free reuse with attribution.
- Kaggle datasets: licences vary per dataset (CC0, CC BY-NC, ODbL). **Check each licence
  before redistributing derived data in the repo.** Safer pattern: commit the *fitted
  distribution parameters*, not the source rows — smaller, licence-clean, and it keeps the
  seed deterministic.
- Plaid/Teller sandbox: development use permitted under their terms; no real consumer data.
- Dodo: never commit keys; use a read-only key for ingestion.

## Verification Log

| Endpoint | Result |
|---|---|
| `api.frankfurter.dev/v1/latest` | 200, live rates returned |
| `api.frankfurter.dev/v1/<range>` | 200, time series returned |
| `data.sec.gov/api/xbrl/frames/...` | 200, 3,263 data points |
| `data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json` | 200 |
| `api.fiscaldata.treasury.gov/.../avg_interest_rates` | 200, records from 2001 |
| `api.fiscaldata.treasury.gov/.../rates_of_exchange` | 200 |
| `data-api.ecb.europa.eu/service/data/EXR/...` | 200, ECB JSON |
| `data-api.ecb.europa.eu/service/data/YC/...` | 200 |
| `api.worldbank.org/v2/country/US/indicator/FR.INR.LEND` | 200 |
| `home.treasury.gov` daily yield curve CSV | 200 |
| `api.stlouisfed.org/fred/series` | 400 (alive, key required) |
| `docs.dodopayments.com` API reference | 200, endpoint list read |
| Kaggle: IBM late payment, Olist, PaySim, payment-date, creditcardfraud | 200 |
| UCI: Online Retail II, Default of Credit Card Clients | 200 |
| `plaid.com/docs/sandbox`, `teller.io/docs` | 200 |
| GoCardless Bank Account Data | 302 — confirm current URL |
