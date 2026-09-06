"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AgentConsole } from "@/components/agents/AgentConsole";
import { NoSource, SourceLine } from "@/components/SourceBanner";
import { Panel } from "@/components/primitives";
import { ActivityFeed } from "@/components/warroom/ActivityFeed";
import { AgentLanes } from "@/components/warroom/AgentLanes";
import { ConflictPanel } from "@/components/warroom/ConflictPanel";
import { InvestigationHeader } from "@/components/warroom/InvestigationHeader";
import { apiUrl, fetchJson } from "@/lib/api";
import type { WarRoomEvent } from "@/lib/events";
import { getDataSource, type DataSourceStatus } from "@/lib/position";
import { project, scopeToInvestigation } from "@/lib/warroom";

/**
 * The War Room: the room you walk into, not a report you are shown.
 *
 * It opens on the agents, because that is what an analyst does here — pick a specialist,
 * point it at the loaded ledger, read what it found. Everything below the console is the
 * escalation path: a war room proper opens only on a policy breach the check actually
 * found, and until then this screen says so plainly instead of inventing an incident.
 *
 * The feed is folded from the event stream, live over SSE and replayed from history on
 * load, so a tab opened halfway through renders identically to one that watched from the
 * start.
 */

interface WarRoomStatus {
  open: boolean;
  escalate: boolean;
  result: {
    investigation_id: string;
    phase: string;
    trigger: string;
    recommendation?: { recommendation_id: string } | null;
  } | null;
  next_seq: number;
}

interface CycleStatus {
  has_run: boolean;
  published: { version_id: string; published_by: string } | null;
  policy: { escalate: boolean; worst: { description: string } | null } | null;
}

export default function WarRoomScreen() {
  const [status, setStatus] = useState<DataSourceStatus | null>(null);
  const [cycle, setCycle] = useState<CycleStatus | null>(null);
  const [warRoom, setWarRoom] = useState<WarRoomStatus | null>(null);
  const [events, setEvents] = useState<WarRoomEvent[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [source, cycleStatus, room] = await Promise.all([
        getDataSource(),
        fetchJson<CycleStatus>("/api/cycle"),
        fetchJson<WarRoomStatus>("/api/war-room"),
      ]);
      setStatus(source);
      setCycle(cycleStatus);
      setWarRoom(room);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  // Kicked off as a promise chain rather than awaited in the effect body: the roster and
  // the cycle are external systems this screen subscribes to, and setting state from the
  // resolved promise keeps the effect itself free of synchronous renders.
  useEffect(() => {
    Promise.all([
      getDataSource(),
      fetchJson<CycleStatus>("/api/cycle"),
      fetchJson<WarRoomStatus>("/api/war-room"),
    ])
      .then(([source, cycleStatus, room]) => {
        setStatus(source);
        setCycle(cycleStatus);
        setWarRoom(room);
      })
      .catch((cause: Error) => setError(cause.message));
  }, []);

  // One subscription for the life of the screen. The bus replays from seq 0, so this is
  // history and live traffic through the same path -- there is no second code path for
  // "what happened before I arrived" to drift away from.
  useEffect(() => {
    const stream = new EventSource(apiUrl("/api/war-room/events?replay_from=0"));
    stream.onmessage = (message) => {
      try {
        const parsed = JSON.parse(message.data) as WarRoomEvent;
        setEvents((previous) => [...previous, parsed]);
      } catch {
        // A malformed frame is dropped rather than taking the screen down with it.
      }
    };
    stream.onerror = () => stream.close();
    return () => stream.close();
  }, [status?.source?.tenant_id]);

  const act = useCallback(
    async (label: string, path: string, body?: unknown) => {
      setBusy(label);
      setError(null);
      try {
        await fetchJson(path, { method: "POST", body: JSON.stringify(body ?? {}) });
        await refresh();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        setBusy(null);
      }
    },
    [refresh],
  );

  if (status && status.source === null) return <NoSource what="The War Room" />;

  const investigationId = warRoom?.result?.investigation_id ?? null;
  const view = project(investigationId ? scopeToInvestigation(events, investigationId) : events);

  const steps: { label: string; done: boolean; run: () => void; why: string }[] = [
    {
      label: "Run the weekly cycle",
      done: Boolean(cycle?.has_run),
      run: () => act("cycle", "/api/cycle/run"),
      why: "Refresh actuals, build the bridge, reforecast 13 weeks, surface exceptions.",
    },
    {
      label: "Publish the version",
      done: Boolean(cycle?.published),
      run: () =>
        act("publish", "/api/cycle/publish", {
          published_by: "treasurer@warroom",
          published_by_role: "treasurer",
        }),
      why: "A human signs the version. The policy check only ever runs against a signed one.",
    },
    {
      label: "Check it against policy",
      done: Boolean(cycle?.policy),
      run: () => act("policy", "/api/cycle/policy-check"),
      why: "Deterministic constraint evaluation. This is what decides whether there is an incident.",
    },
    {
      label: "Open the war room",
      done: Boolean(warRoom?.open),
      run: () => act("open", "/api/war-room/open"),
      why: "Opens only on a dated, quantified breach — never on a request alone.",
    },
  ];

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-4 px-4 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold text-ink">War Room</h1>
        {status?.source ? (
          <SourceLine
            source={status.source}
            extra={`${status.agent_runs} agent run${status.agent_runs === 1 ? "" : "s"}`}
          />
        ) : null}
      </div>

      {error ? (
        <p className="rounded-lg border border-critical bg-surface-1 px-4 py-3 text-xs leading-relaxed text-critical">
          ✗ {error}
        </p>
      ) : null}

      <p className="max-w-4xl text-xs leading-relaxed text-ink-2">
        Every figure on every screen comes from this ledger and from the agents you run against it
        here. Start a specialist below and its finding lands on the Forecast, Recommendation and
        Evidence screens with citations that resolve back to the rows it actually read.
      </p>

      <AgentConsole onRan={refresh} />

      <Panel
        title="Escalation"
        subtitle={
          warRoom?.open
            ? "a war room is open"
            : cycle?.policy
              ? cycle.policy.escalate
                ? "policy breached — a war room can open"
                : "within policy — nothing to escalate"
              : "not yet checked"
        }
      >
        <ol className="divide-y divide-line">
          {steps.map((step, index) => (
            <li key={step.label} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
              <span className={`text-xs ${step.done ? "text-good" : "text-ink-3"}`} aria-hidden>
                {step.done ? "✓" : `${index + 1}.`}
              </span>
              <div className="flex min-w-[16rem] flex-1 flex-col">
                <span className="text-xs font-medium text-ink">{step.label}</span>
                <span className="text-[11px] leading-relaxed text-ink-3">{step.why}</span>
              </div>
              <button
                type="button"
                onClick={step.run}
                disabled={busy !== null}
                className="rounded border border-accent px-3 py-1.5 text-xs text-accent hover:bg-surface-2 disabled:border-line disabled:text-ink-3"
              >
                {busy ? "Working…" : step.done ? "Run again" : "Run"}
              </button>
            </li>
          ))}
        </ol>
        {cycle?.policy && !cycle.policy.escalate ? (
          <p className="border-t border-line px-4 py-3 text-[11px] leading-relaxed text-ink-2">
            The published version is inside every hard constraint, so there is nothing to escalate.
            If this tenant&rsquo;s real liquidity floor is not the one configured, set it on the
            Forecast screen and check again — a war room opened without a breach is a war room
            nobody trusts.
          </p>
        ) : null}
      </Panel>

      {warRoom?.open ? (
        <>
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
        </>
      ) : (
        <ActivityFeed lines={view.feed} />
      )}
    </div>
  );
}
