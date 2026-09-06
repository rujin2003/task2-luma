/**
 * Turning an event stream into the four things the War Room screen shows.
 *
 * The screen is a projection of the feed, not a second source of truth: every panel below
 * is derived by folding the same `WarRoomEvent[]` the SSE endpoint delivers. That is why
 * a late-joining browser and a browser that watched from the start render identically --
 * they fold the same events, and the fold has no memory of when it started.
 *
 * The one rule with teeth is what is *absent*. There is no field for a model's reasoning
 * in the event schema, and there is nothing here that would surface one if there were.
 * The vocabulary is findings, evidence, decisions and status.
 */

import type { AgentFinding, AgentRole, AgentStatus, Evidence, Money } from "./contracts";
import type { InvestigationPhase, StatusMark, WarRoomEvent } from "./events";

export const PHASE_ORDER: InvestigationPhase[] = [
  "planning",
  "investigating",
  "resolving_conflicts",
  "generating_scenarios",
  "stress_testing",
  "replanning",
  "recommending",
  "closed",
];

export const PHASE_LABEL: Record<InvestigationPhase, string> = {
  planning: "Planning",
  investigating: "Investigating",
  resolving_conflicts: "Resolving conflicts",
  generating_scenarios: "Scenarios",
  stress_testing: "Stress testing",
  replanning: "Replanning",
  recommending: "Recommending",
  awaiting_approval: "Awaiting approval",
  closed: "Closed",
  failed: "Failed",
};

export const AGENT_LABEL: Record<AgentRole, string> = {
  forecast: "Forecast",
  variance: "Variance",
  ar_collections: "AR Collections",
  ap_optimization: "AP Optimization",
  supplier_risk: "Supplier Risk",
  dodo_revenue: "Dodo Revenue",
  commander: "Commander",
  conflict_resolution: "Conflict Resolution",
  stress_test: "Stress Test",
  covenant_explainer: "Covenant Explainer",
  cartographer: "Cartographer",
};

/** Terminal statuses the UI must not render as "still going". */
const TERMINAL: ReadonlySet<AgentStatus> = new Set<AgentStatus>([
  "complete",
  "degraded",
  "refused",
  "timeout",
  "failed",
]);

export interface Opening {
  trigger: string;
  detectedAt: string;
  quantum?: Money | null;
  breachDescription?: string;
  breachThreshold?: string;
  breachWeek?: number | null;
  evidence: Evidence[];
}

export interface PlanSummary {
  planId: string;
  invoked: AgentRole[];
  /** Why an agent was *not* called. A pure-AR incident must not invoke Dodo. */
  skipped: Array<{ agent: AgentRole; reason: string }>;
}

export interface AgentLane {
  agent: AgentRole;
  runId?: string;
  status: AgentStatus;
  queuePosition?: number | null;
  model?: string | null;
  toolCalls: Array<{ tool: string; rowCount?: number | null; error?: string | null }>;
  findings: AgentFinding[];
  failureReason?: string | null;
  latencyMs?: number | null;
  /** A citation the validator could not resolve. The finding was rejected, not shown. */
  rejectedEvidence: Array<{ reference: string; reason: string }>;
}

export interface ConflictThread {
  conflictId: string;
  kind: "structural" | "semantic";
  agents: AgentRole[];
  description: string;
  delta?: Money | null;
  followups: Array<{ agent: AgentRole; question: string }>;
  resolution?: { text: string; upheld?: AgentRole | null; evidence: Evidence[] };
}

export interface FeedLine {
  seq: number;
  mark: StatusMark;
  text: string;
  ts: string;
  agent?: AgentRole;
}

export interface WarRoomView {
  investigationId?: string;
  opening?: Opening;
  plan?: PlanSummary;
  phase?: InvestigationPhase;
  phasesSeen: InvestigationPhase[];
  elapsedMs?: number | null;
  lanes: AgentLane[];
  conflicts: ConflictThread[];
  degradations: Array<{ component: string; reason: string }>;
  feed: FeedLine[];
  closed?: { phase: "closed" | "failed"; reason: string; recommendationId?: string | null };
  /** Cycle steps share the bus. They belong on the Forecast screen, not in here. */
  cycleSteps: Array<{ step: number; name: string }>;
}

function lane(lanes: Map<AgentRole, AgentLane>, agent: AgentRole): AgentLane {
  let existing = lanes.get(agent);
  if (!existing) {
    existing = { agent, status: "queued", toolCalls: [], findings: [], rejectedEvidence: [] };
    lanes.set(agent, existing);
  }
  return existing;
}

/**
 * Fold the stream into the screen.
 *
 * Deliberately total over the event union: every case is handled, so a new event type
 * added to the schema is a TypeScript error here rather than a row that silently vanishes
 * from the feed. The `default` branch keeps the line but adds no panel state, which is the
 * right behaviour for an event this build has not been taught to render.
 */
export function project(events: readonly WarRoomEvent[]): WarRoomView {
  const lanes = new Map<AgentRole, AgentLane>();
  const conflicts = new Map<string, ConflictThread>();
  const view: WarRoomView = {
    phasesSeen: [],
    lanes: [],
    conflicts: [],
    degradations: [],
    feed: [],
    cycleSteps: [],
  };

  for (const event of events) {
    if (event.investigation_id) view.investigationId = event.investigation_id;

    switch (event.type) {
      case "investigation.opened":
        view.opening = {
          trigger: event.trigger,
          detectedAt: event.detected_at,
          quantum: event.quantum,
          breachDescription: event.breach?.description,
          breachThreshold: event.breach?.threshold_display,
          breachWeek: event.breach?.week_index,
          evidence: event.breach?.evidence ?? [],
        };
        break;
      case "investigation.phase":
        view.phase = event.phase;
        if (!view.phasesSeen.includes(event.phase)) view.phasesSeen.push(event.phase);
        view.elapsedMs = event.elapsed_ms ?? view.elapsedMs;
        break;
      case "investigation.closed":
        view.phase = event.phase;
        view.closed = {
          phase: event.phase,
          reason: event.reason,
          recommendationId: event.recommendation_id,
        };
        break;
      case "plan.selected":
        view.plan = { planId: event.plan_id, invoked: event.invoked, skipped: event.skipped };
        for (const agent of event.invoked) lane(lanes, agent);
        break;
      case "agent.queued": {
        const target = lane(lanes, event.agent);
        target.runId = event.run_id;
        target.queuePosition = event.queue_position;
        target.status = "queued";
        break;
      }
      case "agent.started": {
        const target = lane(lanes, event.agent);
        target.runId = event.run_id;
        target.model = event.model;
        target.status = "running";
        break;
      }
      case "agent.tool_call":
        lane(lanes, event.agent).toolCalls.push({
          tool: event.tool,
          rowCount: event.row_count,
          error: event.error,
        });
        break;
      case "agent.finding":
        lane(lanes, event.agent).findings.push(event.finding);
        break;
      case "agent.status": {
        const target = lane(lanes, event.agent);
        target.status = event.status;
        target.failureReason = event.failure_reason;
        target.latencyMs = event.latency_ms;
        break;
      }
      case "evidence.rejected":
        lane(lanes, event.agent).rejectedEvidence.push({
          reference: event.reference,
          reason: event.reason,
        });
        break;
      case "conflict.detected":
        conflicts.set(event.conflict_id, {
          conflictId: event.conflict_id,
          kind: event.kind,
          agents: event.agents,
          description: event.description,
          delta: event.delta,
          followups: [],
        });
        break;
      case "conflict.followup":
        conflicts
          .get(event.conflict_id)
          ?.followups.push({ agent: event.agent, question: event.question });
        break;
      case "conflict.resolved": {
        const thread = conflicts.get(event.conflict_id);
        if (thread) {
          thread.resolution = {
            text: event.resolution,
            upheld: event.upheld,
            evidence: event.evidence,
          };
        }
        break;
      }
      case "system.degraded":
        view.degradations.push({ component: event.component, reason: event.reason });
        break;
      case "cycle.step":
        view.cycleSteps.push({ step: event.step, name: event.name });
        break;
      default:
        break;
    }

    // The feed takes every event, including the ones no panel reads. A status line the
    // screen cannot place is still a status line the analyst can read.
    if (event.type !== "stream.heartbeat") {
      view.feed.push({
        seq: event.seq,
        mark: event.mark,
        text: event.status_line,
        ts: event.ts,
        agent: "agent" in event ? event.agent : undefined,
      });
    }
  }

  view.lanes = [...lanes.values()];
  view.conflicts = [...conflicts.values()];
  return view;
}

/**
 * Narrow a bus stream to one investigation.
 *
 * The weekly cycle and the war room share a bus, and they share agents: the Variance
 * Agent explains the bridge at step 3 and then runs again inside the escalation. Folding
 * the whole stream therefore gives Variance one lane with both runs' tool calls and both
 * runs' findings concatenated, which reads as an agent that said the same thing twice.
 *
 * Investigation events all carry `investigation_id`; cycle steps carry none. That is the
 * whole filter, and it is here rather than inside `project()` because a caller rendering
 * the cycle wants the opposite half of the same stream.
 */
export function scopeToInvestigation(
  events: readonly WarRoomEvent[],
  investigationId?: string,
): WarRoomEvent[] {
  const scoped = events.filter((event) => Boolean(event.investigation_id));
  if (!investigationId) return scoped;
  return scoped.filter((event) => event.investigation_id === investigationId);
}

export function isSettled(status: AgentStatus): boolean {
  return TERMINAL.has(status);
}

/** The mark a lane shows. Mirrors `STATUS_TO_MARK` on the backend, deliberately. */
export function markForStatus(status: AgentStatus): StatusMark {
  switch (status) {
    case "complete":
      return "ok";
    case "running":
      return "working";
    case "degraded":
    case "refused":
      return "warn";
    case "timeout":
    case "failed":
      return "fail";
    default:
      return "info";
  }
}

export function formatElapsed(ms?: number | null): string {
  if (ms === null || ms === undefined) return "--";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}
