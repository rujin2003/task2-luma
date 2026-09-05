/**
 * TypeScript mirror of the frozen event schema in `backend/contracts/events.py`.
 *
 * The vocabulary below is pinned on both sides: `tests/unit/test_event_schema_parity.py`
 * reads the `EVENT_TYPES` literals out of this file and fails CI if they no longer match
 * the Python `EventType` enum. Add here and there together, or not at all.
 */

import type {
  AgentFinding,
  AgentRole,
  AgentStatus,
  ApprovalDecision,
  ApprovalRequest,
  ConstraintViolation,
  Evidence,
  Money,
  Recommendation,
  ReplanAttempt,
  StressResult,
  Strategy,
} from "./contracts";

export const EVENT_SCHEMA_VERSION = "1.0.0";

export const EVENT_TYPES = [
  "stream.opened",
  "stream.heartbeat",
  "system.degraded",
  "investigation.opened",
  "investigation.phase",
  "investigation.closed",
  "plan.selected",
  "agent.queued",
  "agent.started",
  "agent.tool_call",
  "agent.finding",
  "agent.status",
  "evidence.rejected",
  "conflict.detected",
  "conflict.followup",
  "conflict.resolved",
  "scenario.generated",
  "stress.completed",
  "replan.started",
  "recommendation.ready",
  "approval.requested",
  "approval.decided",
  "cycle.step",
] as const;

export type EventType = (typeof EVENT_TYPES)[number];

export type StatusMark = "ok" | "warn" | "working" | "fail" | "info";

export const MARK_GLYPH: Record<StatusMark, string> = {
  ok: "✓",
  warn: "⚠",
  working: "↻",
  fail: "✗",
  info: "•",
};

export type InvestigationPhase =
  | "planning"
  | "investigating"
  | "resolving_conflicts"
  | "generating_scenarios"
  | "stress_testing"
  | "replanning"
  | "recommending"
  | "awaiting_approval"
  | "closed"
  | "failed";

export type ConflictKind = "structural" | "semantic";

/**
 * Present on every event. A feed that understands nothing else still renders correctly
 * from `mark` and `status_line`.
 */
export interface EventEnvelope {
  schema_version: string;
  event_id: string;
  seq: number;
  ts: string;
  investigation_id?: string | null;
  mark: StatusMark;
  status_line: string;
}

export interface StreamOpened extends EventEnvelope {
  type: "stream.opened";
  replay_from: number;
  synthetic_data: boolean;
}

export interface StreamHeartbeat extends EventEnvelope {
  type: "stream.heartbeat";
}

export interface SystemDegraded extends EventEnvelope {
  type: "system.degraded";
  component: string;
  reason: string;
}

export interface InvestigationOpened extends EventEnvelope {
  type: "investigation.opened";
  trigger: string;
  detected_at: string;
  quantum?: Money | null;
  breach?: ConstraintViolation | null;
}

export interface InvestigationPhaseChanged extends EventEnvelope {
  type: "investigation.phase";
  phase: InvestigationPhase;
  depth: number;
  elapsed_ms?: number | null;
}

export interface InvestigationClosed extends EventEnvelope {
  type: "investigation.closed";
  phase: "closed" | "failed";
  recommendation_id?: string | null;
  reason: string;
}

export interface SkippedAgent {
  agent: AgentRole;
  reason: string;
}

export interface PlanSelected extends EventEnvelope {
  type: "plan.selected";
  plan_id: string;
  invoked: AgentRole[];
  skipped: SkippedAgent[];
}

export interface AgentQueued extends EventEnvelope {
  type: "agent.queued";
  agent: AgentRole;
  run_id: string;
  queue_position?: number | null;
}

export interface AgentStarted extends EventEnvelope {
  type: "agent.started";
  agent: AgentRole;
  run_id: string;
  model?: string | null;
}

export interface AgentToolCall extends EventEnvelope {
  type: "agent.tool_call";
  agent: AgentRole;
  run_id: string;
  tool: string;
  duration_ms?: number | null;
  row_count?: number | null;
  truncated_from?: number | null;
  error?: string | null;
}

export interface AgentFindingEmitted extends EventEnvelope {
  type: "agent.finding";
  agent: AgentRole;
  run_id: string;
  finding: AgentFinding;
}

export interface AgentStatusChanged extends EventEnvelope {
  type: "agent.status";
  agent: AgentRole;
  run_id: string;
  status: AgentStatus;
  failure_reason?: string | null;
  latency_ms?: number | null;
  attempt: number;
}

export interface EvidenceRejected extends EventEnvelope {
  type: "evidence.rejected";
  agent: AgentRole;
  run_id: string;
  reference: string;
  reason: string;
}

export interface ConflictDetected extends EventEnvelope {
  type: "conflict.detected";
  conflict_id: string;
  kind: ConflictKind;
  agents: AgentRole[];
  description: string;
  delta?: Money | null;
}

export interface FollowupDispatched extends EventEnvelope {
  type: "conflict.followup";
  conflict_id: string;
  agent: AgentRole;
  question: string;
}

export interface ConflictResolved extends EventEnvelope {
  type: "conflict.resolved";
  conflict_id: string;
  resolution: string;
  upheld?: AgentRole | null;
  evidence: Evidence[];
}

export interface ScenarioGenerated extends EventEnvelope {
  type: "scenario.generated";
  strategy: Strategy;
}

export interface StressCompleted extends EventEnvelope {
  type: "stress.completed";
  result: StressResult;
}

export interface ReplanStarted extends EventEnvelope {
  type: "replan.started";
  attempt: ReplanAttempt;
}

export interface RecommendationReady extends EventEnvelope {
  type: "recommendation.ready";
  recommendation: Recommendation;
}

export interface ApprovalRequested extends EventEnvelope {
  type: "approval.requested";
  request: ApprovalRequest;
}

export interface ApprovalDecided extends EventEnvelope {
  type: "approval.decided";
  decision: ApprovalDecision;
}

export interface CycleStepCompleted extends EventEnvelope {
  type: "cycle.step";
  step: number;
  name: string;
  forecast_version_id?: string | null;
}

export type WarRoomEvent =
  | StreamOpened
  | StreamHeartbeat
  | SystemDegraded
  | InvestigationOpened
  | InvestigationPhaseChanged
  | InvestigationClosed
  | PlanSelected
  | AgentQueued
  | AgentStarted
  | AgentToolCall
  | AgentFindingEmitted
  | AgentStatusChanged
  | EvidenceRejected
  | ConflictDetected
  | FollowupDispatched
  | ConflictResolved
  | ScenarioGenerated
  | StressCompleted
  | ReplanStarted
  | RecommendationReady
  | ApprovalRequested
  | ApprovalDecided
  | CycleStepCompleted;

/** One activity-feed line: the glyph and the status line, nothing else. */
export function displayLine(event: WarRoomEvent): string {
  return `${MARK_GLYPH[event.mark]} ${event.status_line}`;
}
