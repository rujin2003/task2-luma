"use client";

import { useCallback, useEffect, useState } from "react";

import { AgentConsole } from "@/components/agents/AgentConsole";
import { NoSource, SourceLine } from "@/components/SourceBanner";
import { getDataSource, type DataSourceStatus } from "@/lib/position";

/**
 * The agent space — the six specialists as things with names, jobs, dependencies and a
 * start button.
 *
 * It is the same console the War Room opens with, because it is the same operation. This
 * screen is the one you come to when you want the roster itself rather than the incident
 * around it: what each agent is for, what it is allowed to call, what it needs first, and
 * what it last concluded.
 */
export default function AgentsScreen() {
  const [status, setStatus] = useState<DataSourceStatus | null>(null);

  const refresh = useCallback(async () => {
    setStatus(await getDataSource());
  }, []);

  useEffect(() => {
    getDataSource()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  if (status && status.source === null) return <NoSource what="The agent space" />;

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-4 px-4 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold text-ink">Agent space</h1>
        {status?.source ? (
          <SourceLine source={status.source} extra={status.provider} />
        ) : (
          <p className="font-mono text-xs text-ink-3">loading…</p>
        )}
      </div>

      <p className="max-w-3xl text-xs leading-relaxed text-ink-2">
        Six specialists, each with a fixed job and a fixed set of tools it is allowed to call.
        Starting one runs it against the loaded ledger through the same runtime the Commander uses —
        so it can refuse, degrade, or disagree with another agent, and every figure it reports
        carries evidence the validator could resolve to a row.
      </p>

      <AgentConsole onRan={refresh} />
    </div>
  );
}
