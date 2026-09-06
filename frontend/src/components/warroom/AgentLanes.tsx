import { formatMoney } from "@/lib/contracts";
import type { AgentFinding, AgentStatus } from "@/lib/contracts";
import { MARK_GLYPH } from "@/lib/events";
import type { AgentLane } from "@/lib/warroom";
import { AGENT_LABEL, formatElapsed, markForStatus } from "@/lib/warroom";

import { Traced } from "../evidence/Traced";
import { Panel } from "../primitives";

const STATUS_LABEL: Record<AgentStatus, string> = {
  queued: "Queued",
  running: "Running",
  complete: "Complete",
  degraded: "Degraded",
  refused: "Refused",
  timeout: "Timed out",
  failed: "Failed",
};

const STATUS_CLASS: Record<AgentStatus, string> = {
  queued: "text-ink-3",
  running: "text-accent",
  complete: "text-good",
  degraded: "text-warning",
  refused: "text-warning",
  timeout: "text-critical",
  failed: "text-critical",
};

/**
 * One lane per agent in the wave, including the ones that did not go well.
 *
 * `queued`, `degraded`, `refused` and `timeout` are rendered exactly as prominently as
 * `complete`. A free tier throttles a nine-agent wave and some of those lanes will be
 * honest failures; hiding them behind a spinner would turn a system that is telling you
 * what it does not know into one that appears to know everything.
 */
export function AgentLanes({ lanes }: { lanes: AgentLane[] }) {
  return (
    <Panel title="Agents" subtitle={`${lanes.length} in the wave`}>
      <ul className="divide-y divide-line">
        {lanes.map((lane) => (
          <li key={lane.agent} className="px-4 py-3">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-sm text-ink">{AGENT_LABEL[lane.agent]}</span>
              <span
                className={`inline-flex items-center gap-1.5 text-xs ${STATUS_CLASS[lane.status]}`}
              >
                <span aria-hidden>{MARK_GLYPH[markForStatus(lane.status)]}</span>
                <span>{STATUS_LABEL[lane.status]}</span>
                {lane.status === "queued" && lane.queuePosition != null ? (
                  <span className="text-ink-3">#{lane.queuePosition}</span>
                ) : null}
              </span>
            </div>

            <div className="mt-1 flex flex-wrap gap-x-4 font-mono text-xs text-ink-3">
              {lane.model ? <span>{lane.model}</span> : null}
              <span>{formatElapsed(lane.latencyMs)}</span>
              <span>
                {lane.toolCalls.length} tool call{lane.toolCalls.length === 1 ? "" : "s"}
              </span>
            </div>

            {/* Tool names, never tool arguments. */}
            {lane.toolCalls.length > 0 ? (
              <p className="mt-1 font-mono text-xs break-words text-ink-3">
                {lane.toolCalls.map((call) => call.tool).join(" · ")}
              </p>
            ) : null}

            {lane.failureReason ? (
              <p className="mt-2 text-xs leading-relaxed text-warning">{lane.failureReason}</p>
            ) : null}

            {lane.rejectedEvidence.map((rejected) => (
              <p key={rejected.reference} className="mt-2 text-xs leading-relaxed text-critical">
                ✗ evidence rejected: <span className="font-mono">{rejected.reference}</span> —{" "}
                {rejected.reason}
              </p>
            ))}

            {lane.findings.map((finding, index) => (
              <Finding key={`${lane.agent}-${index}`} finding={finding} />
            ))}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

/** A finding is a claim plus the rows underneath it. Both are shown, or neither is. */
function Finding({ finding }: { finding: AgentFinding }) {
  return (
    <div className="mt-2 border-l-2 border-line pl-3">
      <p className="text-xs leading-relaxed text-ink">{finding.headline}</p>
      {finding.quantum ? (
        <p className="mt-0.5 font-mono text-xs text-ink-2 tabular-nums">
          {formatMoney(finding.quantum, { compact: true })}
        </p>
      ) : null}

      {finding.confidence ? (
        <p className="mt-1 text-xs text-ink-3">
          {/* Measured error and a labelled judgement are different things and are said
              differently, so nobody reads a guess as a statistic. */}
          {finding.confidence.basis === "empirical"
            ? `Measured: MAPE ${finding.confidence.mape_pct}% over ${finding.confidence.sample_size ?? "?"} weeks`
            : `Judgement (${finding.confidence.band ?? "unbanded"}): ${finding.confidence.rationale}`}
        </p>
      ) : null}

      {finding.rejects.length > 0 ? (
        <ul className="mt-1 text-xs text-warning">
          {finding.rejects.map((reject) => (
            <li key={reject}>⚠ {reject}</li>
          ))}
        </ul>
      ) : null}

      <p className="mt-1 flex flex-wrap gap-x-3 font-mono text-xs text-ink-3">
        {finding.evidence.map((evidence) => (
          <Traced key={evidence.reference} reference={evidence.reference} label={finding.headline}>
            {evidence.reference}
          </Traced>
        ))}
      </p>
    </div>
  );
}
