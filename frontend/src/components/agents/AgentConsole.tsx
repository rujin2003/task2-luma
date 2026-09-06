"use client";

import { useCallback, useEffect, useState } from "react";

import { AgentCardView } from "@/components/agents/AgentCardView";
import { AgentGraph } from "@/components/agents/AgentGraph";
import { Panel } from "@/components/primitives";
import { fetchJson } from "@/lib/api";
import { STATUS_MARK, STATUS_TONE, type AgentRun, type Roster } from "@/lib/agents";

/**
 * The operations surface: the six specialists, what each one needs, and a button.
 *
 * This is the same component on the War Room and on the Agent space, because it is the
 * same thing — a room where you point a named agent at the loaded ledger and watch what
 * it says. An agent started here goes through `AgentRunner` exactly as the Commander's
 * wave does: same allowlist, same budget, same evidence validator, same event bus. The
 * only difference is who asked.
 *
 * Runs are serialised by the session lock, so the console starts one at a time and says
 * so rather than firing six requests and rendering whichever returns first.
 */
export function AgentConsole({
  onRan,
  compact = false,
}: {
  /** Called after each run, so a parent screen can refresh the figures it is showing. */
  onRan?: () => void;
  compact?: boolean;
}) {
  const [roster, setRoster] = useState<Roster | null>(null);
  const [running, setRunning] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setRoster(await fetchJson<Roster>("/api/agents"));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    fetchJson<Roster>("/api/agents")
      .then(setRoster)
      .catch((cause: Error) => setError(cause.message));
  }, []);

  const start = useCallback(
    async (role: string) => {
      setRunning(role);
      setSelected(role);
      setError(null);
      try {
        await fetchJson<AgentRun>(`/api/agents/${role}/run`, {
          method: "POST",
          body: JSON.stringify({}),
        });
        await refresh();
        onRan?.();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        setRunning(null);
      }
    },
    [refresh, onRan],
  );

  const runAll = useCallback(async () => {
    // In dependency order, so Supplier Risk meets AP's actual proposals rather than an
    // empty list. The server serialises anyway; this decides *which* order it gets.
    const order = [
      "forecast",
      "ar_collections",
      "ap_optimization",
      "supplier_risk",
      "dodo_revenue",
      "variance",
    ];
    const known = new Set((roster?.agents ?? []).map((card) => card.role));
    for (const role of order.filter((item) => known.has(item))) {
      await start(role);
    }
  }, [roster, start]);

  const cards = roster?.agents ?? [];
  const ran = cards.filter((card) => card.last_run).length;

  return (
    <div className="flex flex-col gap-4">
      {error ? (
        <p className="rounded-lg border border-critical bg-surface-1 px-4 py-3 text-xs leading-relaxed text-critical">
          ✗ {error}
        </p>
      ) : null}

      <Panel
        title="Agent dependencies"
        subtitle={`${ran} of ${cards.length} have produced a finding`}
      >
        <AgentGraph
          cards={cards}
          edges={roster?.edges ?? []}
          selected={selected}
          onSelect={(role) => {
            setSelected(role);
            document.getElementById(`agent-${role}`)?.scrollIntoView({ block: "center" });
          }}
        />
        <div className="flex flex-wrap items-center gap-3 border-t border-line px-4 py-3">
          <button
            type="button"
            onClick={runAll}
            disabled={running !== null || cards.length === 0}
            className="rounded border border-accent px-3 py-1.5 text-xs text-accent hover:bg-surface-2 disabled:border-line disabled:text-ink-3"
          >
            {running ? `Running ${running}…` : "Run every agent, in dependency order"}
          </button>
          <p className="text-[11px] leading-relaxed text-ink-2">
            Each run reads the loaded ledger through its own tool allowlist and returns a structured
            finding with citations the validator resolved. Findings feed the Forecast,
            Recommendation and Evidence screens.
          </p>
        </div>
      </Panel>

      <div className={`grid gap-4 ${compact ? "xl:grid-cols-2" : "lg:grid-cols-2 xl:grid-cols-3"}`}>
        {cards.map((card) => (
          <div key={card.role} id={`agent-${card.role}`}>
            <AgentCardView
              card={card}
              running={running === card.role}
              disabled={running !== null}
              onStart={start}
            />
          </div>
        ))}
      </div>

      {roster && roster.manual_runs.length > 0 ? (
        <Panel title="Runs you started" subtitle="most recent last">
          <ul className="divide-y divide-line">
            {roster.manual_runs.map((run) => (
              <li
                key={run.run_id}
                className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-4 py-2"
              >
                <span className={`text-xs ${STATUS_TONE[run.status]}`} aria-hidden>
                  {STATUS_MARK[run.status]}
                </span>
                <span className="font-mono text-[11px] text-ink-3">{run.run_id}</span>
                <span className="text-xs text-ink">{run.agent}</span>
                <span className="text-xs text-ink-2">
                  {run.finding?.headline ?? run.failure_reason ?? run.status}
                </span>
                <span className="ml-auto font-mono text-[10px] text-ink-3">
                  {run.model} · {run.latency_ms}ms
                </span>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}
    </div>
  );
}
