import { formatMoney } from "@/lib/contracts";
import type { ConflictThread } from "@/lib/warroom";
import { AGENT_LABEL } from "@/lib/warroom";

import { Traced } from "../evidence/Traced";
import { Panel, StatusTag } from "../primitives";

const KIND_LABEL = {
  structural: "Numeric disagreement",
  semantic: "Contradictory recommendations",
} as const;

/**
 * Two agents disagreeing, and what settled it.
 *
 * This is the panel that makes the difference between a demo and a product, so it shows
 * the whole thread rather than the conclusion: the positions, the scoped follow-up that
 * was dispatched, and the evidence the resolution rests on. A resolution that reads "the
 * higher-confidence agent won" is a coin toss with a justification attached, and it would
 * be visible here as a resolution with no rows under it.
 */
export function ConflictPanel({ conflicts }: { conflicts: ConflictThread[] }) {
  if (conflicts.length === 0) {
    return (
      <Panel title="Conflicts" subtitle="none detected">
        <p className="px-4 py-3 text-xs leading-relaxed text-ink-2">
          No agent contradicted another on this incident. Detection is deterministic, so this means
          the findings agreed within tolerance — not that nobody checked.
        </p>
      </Panel>
    );
  }

  return (
    <Panel title="Conflicts" subtitle={`${conflicts.length} detected`}>
      <ul className="divide-y divide-line">
        {conflicts.map((conflict) => (
          <li key={conflict.conflictId} className="px-4 py-3">
            <div className="flex items-start justify-between gap-3">
              <p className="text-sm leading-relaxed text-ink">{conflict.description}</p>
              <StatusTag
                status={conflict.resolution ? "good" : "warning"}
                label={conflict.resolution ? "Resolved" : "Open"}
              />
            </div>

            <p className="mt-1 font-mono text-xs text-ink-3">
              {KIND_LABEL[conflict.kind]} ·{" "}
              {conflict.agents.map((a) => AGENT_LABEL[a]).join(" vs ")}
              {conflict.delta ? ` · Δ ${formatMoney(conflict.delta, { compact: true })}` : ""}
            </p>

            {conflict.followups.map((followup, index) => (
              <p
                key={`${conflict.conflictId}-followup-${index}`}
                className="mt-2 border-l-2 border-line pl-3 text-xs leading-relaxed text-ink-2"
              >
                <span className="text-ink-3">
                  ↻ scoped follow-up to {AGENT_LABEL[followup.agent]}:{" "}
                </span>
                {followup.question}
              </p>
            ))}

            {conflict.resolution ? (
              <div className="mt-2 border-l-2 border-good pl-3">
                <p className="text-xs leading-relaxed text-ink">{conflict.resolution.text}</p>
                {conflict.resolution.upheld ? (
                  <p className="mt-1 text-xs text-ink-3">
                    Upheld: {AGENT_LABEL[conflict.resolution.upheld]}
                  </p>
                ) : null}
                <p className="mt-1 flex flex-wrap gap-x-3 font-mono text-xs text-ink-3">
                  {conflict.resolution.evidence.map((evidence) => (
                    <Traced
                      key={evidence.reference}
                      reference={evidence.reference}
                      label="Resolution evidence"
                    >
                      {evidence.reference}
                    </Traced>
                  ))}
                </p>
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </Panel>
  );
}
