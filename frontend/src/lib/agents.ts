/** Types for the agent space. Mirrors `backend/api/agents.py`. */

import type { DataSource } from "@/lib/position";

export type AgentStatus =
  "queued" | "running" | "complete" | "degraded" | "refused" | "timeout" | "failed";

export interface Evidence {
  reference: string;
  source: string;
  excerpt: string;
}

export interface ProposedAction {
  kind: string;
  rationale: string;
  counterparty?: string | null;
  document_ref?: string | null;
  amount?: { amount: number; currency: string } | null;
  delay_days?: number | null;
}

export interface AgentFinding {
  agent: string;
  status: AgentStatus;
  headline: string;
  detail: string;
  quantum?: { amount: number; currency: string } | null;
  confidence?: { value: number } | null;
  evidence: Evidence[];
  risks: string[];
  recommended_actions: ProposedAction[];
  rejects: string[];
  requires_followup: boolean;
  followup_question?: string | null;
}

export interface ToolCall {
  tool: string;
  duration_ms: number;
  row_count?: number | null;
  error?: string | null;
}

export interface AgentRun {
  run_id: string;
  investigation_id?: string | null;
  agent: string;
  status: AgentStatus;
  model: string;
  started_at: string;
  ended_at?: string | null;
  latency_ms: number;
  usage: { input_tokens?: number; output_tokens?: number };
  tool_calls: ToolCall[];
  finding?: AgentFinding | null;
  failure_reason?: string | null;
  attempts: number;
}

export interface Dependency {
  role: string;
  title: string;
  satisfied: boolean;
  reason: string;
}

export interface AgentCard {
  role: string;
  title: string;
  purpose: string;
  asks: string;
  tools: string[];
  model: string;
  timeout_s: number;
  startable: boolean;
  /** Agents whose output this one reads, each with whether it has produced one yet. */
  depends_on: Dependency[];
  /** Agents that read this one's output. */
  feeds: string[];
  requires_sources: string[];
  missing_sources: string[];
  dependency_note: string;
  ready: boolean;
  blocked_reason: string | null;
  last_run: AgentRun | null;
}

export interface AgentEdge {
  from: string;
  to: string;
  kind: string;
}

export interface Roster {
  company: string | null;
  as_of: string | null;
  provider: string;
  source: DataSource | null;
  agents: AgentCard[];
  manual_runs: AgentRun[];
  edges: AgentEdge[];
}

/** Status marks. Meaning survives greyscale: every mark ships with its word. */
export const STATUS_MARK: Record<AgentStatus, string> = {
  queued: "◔",
  running: "↻",
  complete: "✓",
  degraded: "⚠",
  refused: "⊘",
  timeout: "⏱",
  failed: "✗",
};

export const STATUS_TONE: Record<AgentStatus, string> = {
  queued: "text-ink-3",
  running: "text-accent",
  complete: "text-good",
  degraded: "text-warning",
  refused: "text-serious",
  timeout: "text-serious",
  failed: "text-critical",
};

export function money(amount: { amount: number; currency: string } | null | undefined): string {
  if (!amount) return "—";
  const negative = amount.amount < 0;
  const value = Math.abs(amount.amount);
  const whole = Math.trunc(value / 100).toLocaleString("en-US");
  return `${negative ? "-" : ""}${amount.currency} ${whole}.${String(value % 100).padStart(2, "0")}`;
}
