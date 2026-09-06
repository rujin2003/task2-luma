import Link from "next/link";

import { ActivityFeed } from "@/components/warroom/ActivityFeed";
import { AgentLanes } from "@/components/warroom/AgentLanes";
import { ConflictPanel } from "@/components/warroom/ConflictPanel";
import { InvestigationHeader } from "@/components/warroom/InvestigationHeader";
import { Panel } from "@/components/primitives";
import { warRoomFixture } from "@/fixtures/warroom";
import { project, scopeToInvestigation } from "@/lib/warroom";

/**
 * The War Room — live during an escalation, and absent otherwise.
 *
 * Rendered by folding the recorded event stream, which is the same fold a live SSE client
 * runs. Swapping the fixture for `EventSource("/api/war-room/events")` changes the source
 * and nothing else, because the screen holds no state the stream does not carry.
 */
export default function WarRoomScreen() {
  // Scoped to this investigation: the weekly cycle shares the bus, and the Variance Agent
  // runs in both. An unscoped fold gives Variance one lane carrying two runs' findings.
  const view = project(scopeToInvestigation(warRoomFixture.events, warRoomFixture.investigationId));

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-4 px-4 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold text-ink">War Room</h1>
        <p className="font-mono text-xs text-ink-3">
          {view.investigationId ?? "no investigation"} · schema{" "}
          {warRoomFixture.events[0]?.schema_version ?? "?"}
        </p>
      </div>

      {/* Degradation is a state of the screen, not a toast that disappears. */}
      {view.degradations.length > 0 ? (
        <ul className="rounded-lg border border-warning bg-surface-1 px-4 py-3">
          {view.degradations.map((degradation) => (
            <li key={degradation.component} className="text-xs leading-relaxed text-warning">
              ⚠ {degradation.component}: {degradation.reason}
            </li>
          ))}
        </ul>
      ) : null}

      <InvestigationHeader
        opening={view.opening}
        phase={view.phase}
        phasesSeen={view.phasesSeen}
        elapsedMs={view.elapsedMs}
        plan={view.plan}
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <AgentLanes lanes={view.lanes} />
        <div className="flex flex-col gap-4">
          <ConflictPanel conflicts={view.conflicts} />
          <ActivityFeed lines={view.feed} />
        </div>
      </div>

      {view.closed ? (
        <Panel title="Outcome" subtitle={view.closed.phase === "closed" ? "closed" : "failed"}>
          <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
            <p className="text-xs leading-relaxed text-ink-2">
              {view.closed.reason || "The investigation closed with a recommendation."}
            </p>
            {view.closed.recommendationId ? (
              <Link
                href="/recommendation"
                className="rounded border border-accent px-3 py-1.5 text-xs text-accent hover:bg-surface-2"
              >
                Open the recommendation →
              </Link>
            ) : null}
          </div>
        </Panel>
      ) : null}
    </div>
  );
}
