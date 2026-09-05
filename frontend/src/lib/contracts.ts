/**
 * TypeScript mirror of `backend/contracts/`.
 *
 * Hand-written rather than generated, because the UI needs these to read well. Drift is
 * caught by `tests/unit/test_event_schema_parity.py`, which compares the event-type
 * literals in `events.ts` against the Python enum and fails CI if they diverge.
 */

/** Integer minor units plus an explicit currency. Never a JS number of dollars. */
export interface Money {
  minor_units: number;
  currency: string;
}

export type SourceSystem =
  | "bank"
  | "gl"
  | "ar_ledger"
  | "ap_ledger"
  | "payroll"
  | "tax_calendar"
  | "debt"
  | "dodo"
  | "forecast"
  | "policy"
  | "override";

export interface Provenance {
  source_system: SourceSystem;
  record_id: string;
  field?: string | null;
  as_of: string;
  retrieved_at: string;
  is_synthetic: boolean;
}

export interface Evidence {
  reference: string;
  source: SourceSystem;
  excerpt: string;
  provenance?: Provenance | null;
}

export type AgentRole =
  | "forecast"
  | "variance"
  | "ar_collections"
  | "ap_optimization"
  | "supplier_risk"
  | "dodo_revenue"
  | "commander"
  | "conflict_resolution"
  | "stress_test"
  | "covenant_explainer"
  | "cartographer";

export type AgentStatus =
  "queued" | "running" | "complete" | "degraded" | "refused" | "timeout" | "failed";

export type ActionKind =
  | "collection_call"
  | "early_pay_discount"
  | "dispute_resolution"
  | "ap_defer"
  | "ap_accelerate"
  | "dodo_retry"
  | "dodo_dunning"
  | "revolver_draw"
  | "assumption_review";

export interface ProposedAction {
  kind: ActionKind;
  rationale: string;
  counterparty?: string | null;
  document_ref?: string | null;
  amount?: Money | null;
  due_by?: string | null;
  delay_days?: number | null;
  evidence_refs: string[];
}

export type ConfidenceBasis = "empirical" | "qualitative";
export type ConfidenceBand = "high" | "medium" | "low";

/** Measured error or a labelled judgement -- rendered differently, deliberately. */
export interface Confidence {
  basis: ConfidenceBasis;
  rationale: string;
  mape_pct?: string | null;
  sample_size?: number | null;
  horizon_weeks?: number | null;
  band?: ConfidenceBand | null;
}

export interface AgentFinding {
  agent: AgentRole;
  status: AgentStatus;
  headline: string;
  detail: string;
  quantum?: Money | null;
  confidence?: Confidence | null;
  evidence: Evidence[];
  risks: string[];
  recommended_actions: ProposedAction[];
  rejects: string[];
  requires_followup: boolean;
  followup_question?: string | null;
}

export type ConstraintKind =
  | "min_cash"
  | "min_30d_liquidity"
  | "max_revolver_utilization"
  | "covenant_ratio"
  | "protected_payment_class"
  | "max_supplier_delay"
  | "closed_period"
  | "approval_required";

export type ConstraintSeverity = "hard" | "soft";

export interface ConstraintViolation {
  constraint_id: string;
  kind: ConstraintKind;
  severity: ConstraintSeverity;
  description: string;
  observed_money?: Money | null;
  observed_ratio?: string | null;
  observed_days?: number | null;
  threshold_display: string;
  week_index?: number | null;
  evidence: Evidence[];
}

export interface Strategy {
  strategy_id: string;
  name: string;
  actions: ProposedAction[];
  projected_min_cash?: Money | null;
  projected_min_cash_week?: number | null;
  net_cash_impact?: Money | null;
  financing_cost?: Money | null;
  constraint_violations: ConstraintViolation[];
}

export interface Stressor {
  stressor_id: string;
  label: string;
  category: string;
  shift_pct: string;
  calibration: string;
}

export interface StressResult {
  strategy_id: string;
  stressor: Stressor;
  min_cash: Money;
  min_cash_week: number;
  floor: Money;
  passed: boolean;
  headroom: Money;
}

export interface ReplanAttempt {
  attempt: number;
  strategy_id: string;
  failure_reason: string;
  tightened_constraint?: string | null;
}

export type WorklistStatus =
  "open" | "queued" | "needs_approval" | "approved" | "rejected" | "executed" | "landed" | "missed";

export interface WorklistItem {
  seq: number;
  owner: string;
  action: string;
  counterparty?: string | null;
  document_ref?: string | null;
  amount: Money;
  due_date: string;
  status: WorklistStatus;
  proposed_by?: AgentRole | null;
  expected_cash_impact?: Money | null;
  probability_pct?: string | null;
  evidence: Evidence[];
  approval_request_id?: string | null;
}

export interface RejectedAction {
  action: ProposedAction;
  rejected_by: string;
  reason: string;
  violation?: ConstraintViolation | null;
  evidence: Evidence[];
}

export interface Recommendation {
  recommendation_id: string;
  investigation_id?: string | null;
  selected_strategy: Strategy;
  alternatives: Strategy[];
  worklist: WorklistItem[];
  rejected_actions: RejectedAction[];
  stress_results: StressResult[];
  replan_history: ReplanAttempt[];
  degraded: boolean;
  degradation_reason?: string | null;
  requires_human_review: boolean;
  summary: string;
}

export type ApprovalRole = "analyst" | "treasurer" | "cfo" | "board";

export type ApprovalState = "draft" | "pending" | "approved" | "rejected" | "blocked" | "expired";

/** The card renders exactly these eight, in this order. It is a contract, not a layout. */
export const APPROVAL_CARD_FIELDS = [
  "ACTION",
  "AMOUNT",
  "EXPECTED IMPACT",
  "RISK",
  "EVIDENCE",
  "WHY RECOMMENDED",
  "WHAT COULD GO WRONG",
  "APPROVAL REQUIRED",
] as const;

export interface ApprovalRequest {
  request_id: string;
  worklist_seq?: number | null;
  recommendation_id?: string | null;
  action: string;
  amount: Money;
  expected_impact: string;
  risk: string;
  evidence: Evidence[];
  why_recommended: string;
  what_could_go_wrong: string;
  approval_required: ApprovalRole;
  prepared_by: string;
  state: ApprovalState;
  created_at: string;
  data_snapshot_ref?: string | null;
}

export interface ApprovalDecision {
  request_id: string;
  decided_by: string;
  decided_by_role: ApprovalRole;
  approved: boolean;
  reason: string;
  decided_at: string;
  data_snapshot_ref?: string | null;
}

/** Mirrors `Money.__str__` on the backend, then abbreviates for grid density. */
export function formatMoney(money: Money, { compact = false }: { compact?: boolean } = {}): string {
  const major = money.minor_units / 100;
  if (!compact) {
    return `${major.toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })} ${money.currency}`;
  }
  const sign = major < 0 ? "-" : "";
  const magnitude = Math.abs(major);
  if (magnitude >= 1_000_000) return `${sign}$${(magnitude / 1_000_000).toFixed(1)}M`;
  if (magnitude >= 1_000) return `${sign}$${(magnitude / 1_000).toFixed(0)}K`;
  return `${sign}$${magnitude.toFixed(0)}`;
}
