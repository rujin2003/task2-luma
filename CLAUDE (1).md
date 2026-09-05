# WAR ROOM — Autonomous Treasury Crisis-Response System

## Project Overview

WAR ROOM is an AI-powered treasury decision-support platform for CFOs and treasury teams.

Its purpose is not merely to forecast cash. It detects emerging liquidity problems, investigates their causes across multiple financial data sources, generates possible interventions, evaluates trade-offs and constraints, stress-tests proposed plans, and presents a human-approvable action plan.

The system should feel like an autonomous financial "war room" that assembles specialized agents around a concrete treasury problem.

### Core product thesis

> When a company's financial position changes unexpectedly, WAR ROOM assembles specialized AI agents to investigate what happened, determine what can be done, stress-test the available strategies, and deliver an evidence-backed response plan to the CFO.

---

# Important Development Context

## AO

AO (Agent Orchestrator) is the development tool used to build this project.

**AO is NOT part of the WAR ROOM runtime architecture.**

Do not add AO as a runtime dependency or pretend that WAR ROOM's financial agents are AO agents.

AO is being used to coordinate software-development agents/workers while building the product.

The finished application should be a normal standalone application with its own agent runtime.

---

# Product Goals

WAR ROOM should demonstrate:

1. Multi-agent financial investigation
2. Dynamic task decomposition
3. Parallel agent execution
4. Evidence-backed reasoning
5. Agent disagreement and conflict resolution
6. Scenario generation
7. Constraint-aware financial planning
8. Stress testing
9. Iterative replanning
10. Human-in-the-loop approval
11. Clear provenance for financial conclusions
12. Real integration with Dodo Payments

The system must demonstrate genuine agentic behavior.

Avoid building a collection of independent chatbots that simply summarize different datasets.

---

# Primary User

The primary user is a CFO, Treasurer, or senior finance professional.

They should be able to answer:

- Why is liquidity deteriorating?
- What caused the cash forecast to change?
- How much cash is at risk?
- Which receivables can realistically be accelerated?
- Which payments can safely be delayed?
- How much debt capacity is available?
- Are we approaching a covenant breach?
- What FX exposures matter?
- What actions can restore liquidity?
- Which proposed plans survive downside scenarios?
- Which actions require human approval?

---

# Core User Journey

A typical workflow:

1. Financial monitoring detects a liquidity issue.
2. WAR ROOM creates an investigation.
3. The Commander/Orchestrator determines which specialist agents are needed.
4. Agents investigate relevant financial domains in parallel.
5. Agents return structured findings with evidence and confidence.
6. The system identifies conflicts or missing information.
7. Additional investigation is launched where necessary.
8. Candidate liquidity strategies are generated.
9. Strategies are checked against hard constraints.
10. Remaining strategies are stress-tested.
11. Failed strategies trigger replanning.
12. The system selects the best feasible strategy.
13. The CFO receives an evidence-backed recommendation.
14. High-risk actions require explicit human approval.
15. Approved actions may be executed through appropriate integrations.

---

# Reference Scenario

Use a fictional company such as **NovaTech** for demos and seed data.

Example characteristics:

- Annual revenue: ~$450M
- Multiple bank accounts
- Multiple currencies
- Multiple subsidiaries
- Multiple customers and vendors
- Revolving credit facility
- Debt covenants
- Dodo Payments customer collections
- Treasury liquidity policy

Example policy:

- Minimum unrestricted cash: $15M
- Minimum 30-day projected liquidity: $20M
- Maximum revolver utilization: 70%
- Payroll cannot be delayed
- Taxes cannot be delayed
- Strategic suppliers have maximum permitted payment delays

These values should be configurable rather than hard-coded.

---

# System Architecture

Conceptually:

```text
                         CFO
                          |
                          v
                 +------------------+
                 |  WAR ROOM        |
                 |  COMMANDER       |
                 +--------+---------+
                          |
        +-----------------+-----------------+
        |                 |                 |
        v                 v                 v
    Cash Agent         AR Agent          AP Agent
        |                 |                 |
        v                 v                 v
   Cash position      Collections      Payment flexibility
        |                 |                 |
        +-----------------+-----------------+
                          |
          +---------------+---------------+
          |               |               |
          v               v               v
       Dodo Agent      Debt Agent       FX Agent
          |               |               |
          v               v               v
     Payment/revenue   Facilities      Currency exposure
          |               |               |
          +---------------+---------------+
                          |
                          v
                  Covenant / Risk Agent
                          |
                          v
                  Scenario Agent
                          |
                          v
                  Stress Test Agent
                          |
                          v
                    Recommendation
                          |
                 +--------+--------+
                 |                 |
                 v                 v
           Auto-safe actions   CFO approval
```

This is a conceptual architecture, not a requirement that every component must be implemented as a separate process.

---

# Agent Responsibilities

## 1. Commander / Treasury Orchestrator

The central application-level agent.

Responsibilities:

- Understand the financial objective
- Determine which specialist agents are relevant
- Launch investigations
- Track agent results
- Detect contradictory findings
- Request follow-up investigations
- Coordinate scenario generation
- Ensure constraints are checked
- Trigger replanning after failed stress tests
- Produce the final recommendation

The Commander should NOT blindly call every agent for every problem.

It should dynamically determine the investigation plan.

---

## 2. Cash Position Agent

Purpose:

Establish the company's current cash truth.

Responsibilities:

- Aggregate bank balances
- Identify restricted cash
- Compare bank data with accounting cash accounts
- Identify pending transactions
- Detect discrepancies
- Produce current unrestricted liquidity

Output should include:

- Current cash
- Restricted cash
- Unrestricted cash
- Pending cash movements
- Data quality issues
- Evidence references

---

## 3. Cash Forecast Agent

Purpose:

Construct forward-looking liquidity projections.

Responsibilities:

- Forecast cash inflows
- Forecast cash outflows
- Incorporate AR
- Incorporate AP
- Incorporate payroll
- Incorporate tax
- Incorporate debt service
- Incorporate Dodo expected collections
- Incorporate FX obligations
- Produce 30/60/90-day scenarios

Every major forecast component should include:

- Source
- Assumption
- Confidence
- Timestamp

---

## 4. Dodo Revenue / Payments Agent

Dodo Payments is a real financial signal in the system, not a decorative checkout integration.

Responsibilities:

- Ingest Dodo payment events
- Track successful payments
- Track failed payments
- Track refunds
- Track subscription lifecycle events where relevant
- Detect abnormal payment-success trends
- Estimate cash at risk
- Identify recoverable failed payments
- Feed expected collections into cash forecasting

Dodo should materially affect treasury decisions.

Example:

```text
Expected Dodo collections: $2.8M
At-risk collections:       $900K
Expected recovery:         $620K
```

Do not invent Dodo API behavior. Consult current Dodo documentation before implementing integrations.

---

## 5. AR Collections Agent

Purpose:

Determine how much receivables can realistically be accelerated.

Responsibilities:

- Analyze aging
- Analyze historical customer payment behavior
- Identify overdue invoices
- Identify disputed invoices
- Estimate early-payment probability
- Rank collection opportunities
- Estimate realistic accelerated cash

Do not assume all outstanding AR is collectible immediately.

---

## 6. AP Optimization Agent

Purpose:

Determine which outgoing payments can safely be deferred.

Responsibilities:

- Analyze payment schedule
- Understand vendor criticality
- Check contractual/payment constraints
- Identify discretionary payments
- Estimate deferral amount
- Estimate operational impact

Never recommend delaying:

- Payroll
- Taxes
- Legally critical obligations
- Payments explicitly protected by policy

---

## 7. Supplier Risk Agent

Purpose:

Challenge AP recommendations.

It should evaluate:

- Supplier criticality
- Historical disruption risk
- Replacement difficulty
- Contractual penalties
- Operational dependencies

This agent should be capable of disagreeing with the AP Agent.

---

## 8. Debt Agent

Purpose:

Determine financing options.

Responsibilities:

- Identify available credit facilities
- Calculate available capacity
- Estimate borrowing costs
- Determine draw options
- Provide financing alternatives

It should not independently approve borrowing.

---

## 9. Covenant Agent

Purpose:

Enforce hard financial constraints.

Examples:

```text
Net Debt / EBITDA < 3.5x
Revolver utilization <= 70%
Minimum unrestricted cash >= $15M
```

The covenant engine should be deterministic wherever possible.

LLMs may explain constraints but should not be the sole source of truth for numerical covenant calculations.

---

## 10. FX Agent

Purpose:

Analyze currency-related liquidity risk.

Responsibilities:

- Identify currency balances
- Identify upcoming currency obligations
- Analyze existing hedges
- Identify currency mismatches
- Suggest liquidity-preserving FX actions

Avoid making speculative market predictions.

---

## 11. Scenario Agent

Purpose:

Generate candidate liquidity strategies.

A strategy is a combination of possible interventions.

Example:

```text
Strategy A:
- Accelerate $2.6M AR
- Defer $700K AP
- Draw $1M revolver
- Optimize $300K FX

Strategy B:
- Accelerate $1.5M AR
- Defer $1.4M AP
- No debt draw
```

The Scenario Agent should generate multiple alternatives rather than immediately choosing one.

---

## 12. Stress Test Agent

Purpose:

Determine whether a strategy remains viable under adverse conditions.

Possible stressors:

- AR recovery lower than expected
- Revenue decline
- Payment failures increase
- FX shock
- Unexpected tax payment
- Supplier acceleration
- Higher financing cost
- Customer default

Example:

```text
                 Minimum Cash

Base Case          $21.4M
AR -10%            $19.2M
AR -20%            $17.1M
Revenue -15%       $15.8M
Combined Stress    $14.1M  FAIL
```

If the recommended strategy fails a required stress threshold, the Commander should replan.

---

# Agent Output Contract

Agents should return structured outputs rather than prose-only responses.

Example:

```json
{
  "agent": "ar_collections",
  "status": "complete",
  "finding": {
    "potential_accelerated_cash": 2600000,
    "confidence": 0.87
  },
  "evidence": [
    {
      "source": "ar_ledger",
      "reference": "customer_A_invoice_1832"
    }
  ],
  "risks": [
    "Customer C has active dispute"
  ],
  "recommended_actions": [
    "Prioritize Customer A and Customer B"
  ],
  "requires_followup": false
}
```

Use structured schemas wherever possible.

---

# Evidence and Provenance

Financial conclusions must be traceable.

Every important recommendation should be explainable through:

```text
Conclusion
   |
   +-- Agent finding
   |
   +-- Source data
   |
   +-- Calculation
   |
   +-- Assumption
   |
   +-- Confidence
```

Example:

```text
$2.6M AR acceleration
    |
    +-- Customer A: $1.2M
    |     +-- 91% historical payment reliability
    |     +-- no active dispute
    |
    +-- Customer B: $900K
    |     +-- 87% historical reliability
    |
    +-- Customer C: excluded
          +-- 43% recovery probability
          +-- active dispute
```

Never fabricate evidence or provenance.

---

# Decision Model

The system should optimize across multiple dimensions.

Conceptually:

```text
Minimize:

  liquidity shortfall
+ financing cost
+ supplier disruption risk
+ customer relationship risk
+ FX risk
+ covenant risk
+ operational risk
```

Subject to configurable hard constraints:

```text
cash >= minimum_cash

30_day_liquidity >= minimum_liquidity

covenant_ratio <= maximum_covenant_ratio

payroll_delay = 0

tax_delay = 0

critical_supplier_delay <= allowed_delay
```

Numerical optimization should be deterministic and testable.

Use agents for reasoning, investigation, planning, and interpretation; use conventional code for exact calculations and constraint enforcement.

---

# Human-in-the-Loop

WAR ROOM must distinguish between:

### Low-risk / reversible actions

Potentially eligible for automatic execution depending on configuration.

### High-risk / irreversible actions

Require explicit human approval.

Examples:

- Drawing debt
- Large payment changes
- Significant supplier delays
- Large FX transactions
- Customer collection actions with material relationship impact

The UI should clearly state:

```text
ACTION
AMOUNT
EXPECTED IMPACT
RISK
EVIDENCE
WHY RECOMMENDED
WHAT COULD GO WRONG
APPROVAL REQUIRED
```

Never execute consequential financial actions silently.

---

# Dodo Integration

Dodo should be integrated as a first-class financial data source.

Potential flow:

```text
Dodo
  |
  v
Webhook / API ingestion
  |
  v
Normalized payment events
  |
  +--> Dodo Agent
  |
  +--> AR / Revenue analysis
  |
  +--> Cash Forecast
  |
  v
Liquidity decision-making
```

Use webhook-driven events where appropriate.

The exact Dodo endpoints, event names, authentication requirements, and API behavior must be verified against the current official Dodo documentation before implementation.

Keep provider-specific code isolated behind an integration layer.

---

# Suggested Technology Architecture

Use a pragmatic stack.

A reasonable default:

### Frontend

- Next.js
- TypeScript
- Tailwind CSS
- Recharts or equivalent visualization library

### Backend

- Python + FastAPI

Python is preferred for financial analysis, simulation, and agent tooling unless there is a strong reason to use another language.

### Data

- PostgreSQL
- SQLAlchemy / SQLModel
- Pandas for analytical workloads where appropriate

### Agent Runtime

Use the chosen LLM/agent framework for the application-level financial agents.

Keep agent interfaces clean and provider-agnostic.

### Integrations

- Dodo Payments
- Simulated bank feed
- Simulated ERP/GL
- Simulated AR/AP systems

Do not overbuild real enterprise integrations for the demo.

---

# Data Model

At minimum, model:

```text
Company
Subsidiary
BankAccount
BankTransaction
GLAccount
GLTransaction
Customer
Invoice
Payment
Vendor
VendorInvoice
PaymentSchedule
DebtFacility
DebtCovenant
FXPosition
FXHedge
Subscription
FinancialEvent
CashForecast
Scenario
StressTest
Recommendation
Approval
AgentRun
Evidence
```

Use IDs and timestamps consistently.

Money should not be represented as floating-point values where exact monetary arithmetic is required.

Prefer integer minor units or Decimal.

Always store currency explicitly.

---

# Synthetic Data

The demo should use realistic synthetic data.

Do not use fake random numbers without financial relationships.

Create internally consistent data.

For example:

```text
Invoice
  -> Customer
  -> Due date
  -> Payment history
  -> Dodo payment event where appropriate
  -> AR balance
  -> Cash forecast contribution
```

Likewise:

```text
Vendor invoice
  -> Vendor
  -> Payment schedule
  -> AP balance
  -> Cash forecast contribution
```

Inject deliberate anomalies and crisis conditions.

Examples:

- Dodo payment success rate suddenly decreases
- Major customer delays payment
- Supplier accelerates payment request
- Bank/GL discrepancy
- FX obligation increases
- Covenant headroom shrinks
- Forecast assumption becomes stale

---

# Demo Scenario

The main demo should be deterministic.

### Initial state

```text
Unrestricted cash: $23.8M
Minimum required:  $20.0M
Status: HEALTHY
```

### Trigger shock

Simulate:

```text
Major customer delays $4M payment
Dodo payment failure rate increases
Supplier accelerates $1M payment
EUR obligation increases
```

### System response

```text
Liquidity warning
        |
        v
War Room opened
        |
        v
Commander decomposes investigation
        |
        v
Agents investigate in parallel
        |
        v
Findings conflict
        |
        v
Follow-up investigation
        |
        v
Scenario generation
        |
        v
Stress testing
        |
        v
Initial plan fails
        |
        v
Replanning
        |
        v
Robust plan generated
        |
        v
CFO approval
```

This should be the golden-path demo.

---

# UI Requirements

The interface should feel like a CFO command center, not a generic chatbot.

Important screens:

## 1. Executive Overview

Show:

- Current liquidity
- Forecast minimum
- Liquidity threshold
- Cash runway
- Active risks
- Dodo collection health
- Debt capacity
- Open investigations

## 2. War Room

Show:

- Current financial incident
- Agent activity
- Findings
- Conflicts
- Investigation status
- Scenario generation
- Stress tests
- Recommendation

## 3. Recommendation

Show:

- Recommended actions
- Cash impact
- Risks
- Constraints
- Stress results
- Required approvals

## 4. Evidence Explorer

Allow users to inspect why a recommendation was produced.

## 5. Scenario Comparison

Compare candidate strategies side-by-side.

---

# Agent UX

Agent activity should be visible but not overwhelming.

Good:

```text
✓ Dodo Agent
  Identified $900K at-risk collections

✓ AR Agent
  Found $2.6M realistic acceleration opportunity

⚠ Supplier Risk Agent
  Rejected $1.1M proposed AP deferral

✓ Covenant Agent
  Maximum safe revolver draw: $2.2M

⚠ Stress Test
  Strategy #4 fails downside scenario

↻ Commander
  Replanning...
```

Avoid exposing raw chain-of-thought.

Show concise findings, evidence, decisions, and status instead.

---

# Safety and Financial Integrity

This is a financial decision-support system.

Prioritize correctness over flashy autonomy.

Rules:

1. Never fabricate financial data.
2. Never fabricate evidence.
3. Never silently execute high-risk financial actions.
4. Use deterministic code for financial arithmetic.
5. Clearly distinguish actual data, estimates, assumptions, and recommendations.
6. Display confidence where appropriate.
7. Preserve audit logs.
8. Make constraints explicit.
9. Require human approval for consequential actions.
10. Make demo/synthetic data clearly distinguishable from production data.

---

# Engineering Principles

## Prefer simple deterministic code for:

- Arithmetic
- Currency conversion using supplied rates
- Covenant calculations
- Cash aggregation
- Threshold checks
- Scenario calculations
- Constraint validation

## Prefer agents for:

- Investigation
- Root-cause analysis
- Evidence interpretation
- Selecting which information to investigate
- Generating hypotheses
- Comparing qualitative trade-offs
- Deciding which specialist agents to involve
- Explaining recommendations

Do not use an LLM where a deterministic function is more reliable.

---

# Multi-Agent Principles

Agents should have:

- Clear responsibilities
- Structured inputs
- Structured outputs
- Limited context
- Explicit evidence
- Confidence
- Failure states

Agents should be able to:

- disagree
- request more information
- flag uncertainty
- reject another agent's recommendation
- trigger follow-up investigation

The Commander should resolve conflicts through evidence and additional investigation, not simply pick the first answer.

---

# Failure Handling

Expect agents and integrations to fail.

Possible failures:

```text
Dodo unavailable
Bank data incomplete
Agent timeout
Contradictory findings
Missing evidence
Invalid scenario
Constraint violation
LLM unavailable
```

The system should degrade gracefully.

Example:

```text
Dodo Agent unavailable

Impact:
Dodo collections cannot be risk-adjusted.

Action:
Use last-known Dodo forecast with reduced confidence.

Status:
⚠ Recommendation requires human review.
```

---

# Testing Strategy

Test at three levels.

## Unit tests

Test:

- Cash calculations
- Covenant calculations
- Scenario calculations
- Stress testing
- Constraint validation
- Currency arithmetic

## Agent tests

Test:

- Structured outputs
- Correct tool usage
- Evidence references
- Conflict handling
- Failure handling

## End-to-end

The golden-path scenario should consistently produce:

```text
Liquidity breach detected
→ War room created
→ Investigation
→ Candidate strategies
→ Stress failure
→ Replan
→ Robust recommendation
→ Human approval
```

The exact numbers can be deterministic in demo mode.

---
