"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { NoSource, SourceLine } from "@/components/SourceBanner";
import { Panel, StatusTag } from "@/components/primitives";
import { StressMatrix } from "@/components/recommendation/StressMatrix";
import { Worklist } from "@/components/recommendation/Worklist";
import { fetchJson } from "@/lib/api";
import type { Recommendation } from "@/lib/contracts";
import { getDataSource, type DataSourceStatus } from "@/lib/position";

/**
 * What the war room concluded for the loaded tenant, and what it declined to conclude.
 *
 * There is no fixture behind this screen. Until an investigation has run against the
 * loaded ledger there is no recommendation, and the screen says which step is missing
 * rather than rendering last week's answer or a demo one.
 */
export default function RecommendationScreen() {
  const [status, setStatus] = useState<DataSourceStatus | null>(null);
  const [recommendation, setRecommendation] = useState<Recommendation | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getDataSource()
      .then(async (next) => {
        setStatus(next);
        if (next.has_recommendation) {
          setRecommendation(await fetchJson<Recommendation>("/api/war-room/recommendation"));
        }
      })
      .catch((cause: Error) => setError(cause.message));
  }, []);

  if (status && status.source === null) return <NoSource what="A recommendation" />;
  if (!status) return <p className="px-4 py-8 text-xs text-ink-3">Loading…</p>;

  if (!recommendation) {
    return (
      <div className="mx-auto flex max-w-2xl flex-col gap-4 px-4 py-16 text-center">
        <h1 className="text-lg font-semibold text-ink">No recommendation yet</h1>
        <p className="text-sm leading-relaxed text-ink-2">
          A recommendation is produced by an investigation, and an investigation opens only on a
          policy breach the check actually found. Nothing has been escalated for{" "}
          {status.source?.company} yet.
        </p>
        <p className="text-xs leading-relaxed text-ink-3">
          {status.has_cycle
            ? "The cycle has run. Publish it, check it against policy, and open the war room if it breaches."
            : "Start with the weekly cycle in the War Room."}
        </p>
        {error ? <p className="text-xs text-critical">✗ {error}</p> : null}
        <div className="flex justify-center">
          <Link
            href="/war-room"
            className="rounded border border-accent px-4 py-2 text-xs text-accent hover:bg-surface-2"
          >
            Open the War Room →
          </Link>
        </div>
      </div>
    );
  }

  const selected = recommendation.selected_strategy;

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-4 px-4 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold text-ink">Recommendation</h1>
        {status.source ? (
          <SourceLine source={status.source} extra={recommendation.recommendation_id} />
        ) : null}
      </div>

      <Panel title={selected.name} subtitle={selected.strategy_id}>
        <div className="px-4 py-3">
          <p className="text-sm leading-relaxed text-ink-2">{recommendation.summary}</p>
          <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2">
            {recommendation.requires_human_review ? (
              <StatusTag status="warning" label="Requires human review" />
            ) : (
              <StatusTag status="good" label="Within delegated authority" />
            )}
            {recommendation.degraded ? (
              <StatusTag
                status="serious"
                label={recommendation.degradation_reason ?? "Produced degraded"}
              />
            ) : null}
            <Link
              href="/approvals"
              className="rounded border border-accent px-3 py-1.5 text-xs text-accent hover:bg-surface-2"
            >
              Review the approval cards →
            </Link>
          </div>
        </div>
      </Panel>

      <Worklist items={recommendation.worklist} />

      <StressMatrix
        results={recommendation.stress_results}
        replans={recommendation.replan_history}
        selectedStrategyId={selected.strategy_id}
      />
    </div>
  );
}
