"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { NoSource, SourceLine } from "@/components/SourceBanner";
import { EvidenceRow, MissingRow } from "@/components/evidence/EvidenceRow";
import { Panel } from "@/components/primitives";
import { fetchJson } from "@/lib/api";
import type { AgentRun } from "@/lib/agents";
import { resolveLive, type EvidenceRecord } from "@/lib/evidence";
import { getDataSource, type DataSourceStatus } from "@/lib/position";

/**
 * The Evidence Explorer, over the ledger that is actually loaded.
 *
 * The left pane is not a catalogue of everything in the database — it is every citation
 * the agents in this session actually made, with the finding each one supports. That is
 * the question a CFO asks: not "what rows exist" but "what is this recommendation
 * standing on". Selecting one resolves it against the source and shows the row.
 *
 * A reference that does not resolve renders as a visible break. That case matters more
 * than the happy one: it is how a fabricated citation is caught, and the whole evidence
 * contract rests on it being impossible to hide.
 */
export default function EvidenceScreen() {
  const [status, setStatus] = useState<DataSourceStatus | null>(null);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string>("");
  const [record, setRecord] = useState<EvidenceRecord | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    getDataSource()
      .then(async (next) => {
        setStatus(next);
        if (next.source) setRuns(await fetchJson<AgentRun[]>("/api/agents/runs"));
      })
      .catch(() => undefined);
  }, []);

  const citations = useMemo(() => {
    const seen = new Map<
      string,
      { reference: string; excerpt: string; agent: string; headline: string }
    >();
    for (const run of runs) {
      for (const item of run.finding?.evidence ?? []) {
        if (seen.has(item.reference)) continue;
        seen.set(item.reference, {
          reference: item.reference,
          excerpt: item.excerpt,
          agent: run.agent,
          headline: run.finding?.headline ?? "",
        });
      }
    }
    return [...seen.values()];
  }, [runs]);

  const results = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return citations;
    return citations.filter(
      (row) =>
        row.reference.toLowerCase().includes(needle) ||
        row.excerpt.toLowerCase().includes(needle) ||
        row.agent.toLowerCase().includes(needle),
    );
  }, [citations, query]);

  const resolve = useCallback(async (reference: string) => {
    setSelected(reference);
    setRecord(null);
    setFailed(false);
    try {
      const row = await resolveLive(reference);
      if (row.resolved) setRecord(row);
      else setFailed(true);
    } catch {
      setFailed(true);
    }
  }, []);

  if (status && status.source === null) return <NoSource what="The Evidence Explorer" />;
  if (!status) return <p className="px-4 py-8 text-xs text-ink-3">Loading…</p>;

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-4 px-4 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold text-ink">Evidence Explorer</h1>
        {status.source ? (
          <SourceLine
            source={status.source}
            extra={`${citations.length} citations from ${runs.length} runs`}
          />
        ) : null}
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <Panel title="Citations" subtitle={`${results.length} matching`}>
          <div className="border-b border-line px-4 py-3">
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search reference, excerpt or agent"
              aria-label="Search evidence"
              className="w-full rounded border border-line bg-surface-2 px-3 py-2 font-mono text-xs text-ink placeholder:text-ink-3 focus-visible:outline-2 focus-visible:outline-accent"
            />
          </div>
          {results.length === 0 ? (
            <p className="px-4 py-6 text-xs leading-relaxed text-ink-2">
              No agent has cited anything yet. Run a specialist in the War Room and every figure it
              reports will appear here with the row it came from.
            </p>
          ) : (
            <ul className="max-h-[70vh] divide-y divide-line overflow-y-auto">
              {results.map((row) => (
                <li key={row.reference}>
                  <button
                    type="button"
                    onClick={() => resolve(row.reference)}
                    className={`flex w-full flex-col gap-1 px-4 py-2.5 text-left hover:bg-surface-2 ${
                      row.reference === selected ? "bg-surface-2" : ""
                    }`}
                  >
                    <span className="font-mono text-[11px] break-all text-accent">
                      {row.reference}
                    </span>
                    <span className="text-[11px] leading-snug text-ink-2">{row.excerpt}</span>
                    <span className="text-[10px] text-ink-3">cited by {row.agent}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Source row" subtitle={selected || "select a citation"}>
          <div className="px-4 py-4">
            {!selected ? (
              <p className="text-xs leading-relaxed text-ink-2">
                Every number an agent reports carries a reference the validator resolved before the
                finding was accepted. Select one to see the row it points at, in the tenant&rsquo;s
                own ledger.
              </p>
            ) : record ? (
              <EvidenceRow record={record} />
            ) : failed ? (
              <MissingRow reference={selected} />
            ) : (
              <p className="text-xs text-ink-3">Resolving…</p>
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
}
