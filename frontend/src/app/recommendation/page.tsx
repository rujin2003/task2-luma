import Link from "next/link";

import { Panel, StatusTag } from "@/components/primitives";
import { RejectedActions } from "@/components/recommendation/RejectedActions";
import { ScenarioMatrix } from "@/components/recommendation/ScenarioMatrix";
import { StressMatrix } from "@/components/recommendation/StressMatrix";
import { Worklist } from "@/components/recommendation/Worklist";
import { approvalsFixture, recommendationFixture } from "@/fixtures/warroom";

/**
 * What the war room concluded, and what it declined to conclude.
 *
 * The worklist is shown before the reasoning that produced it because that is the order
 * the work happens in on a Monday. Everything below it exists so the analyst can argue
 * with the recommendation rather than accept it: the bundles that were not chosen, the
 * stressors each one was tested against, and the levers that were refused.
 */
export default function RecommendationScreen() {
  const { recommendation, strategies, scores } = recommendationFixture;
  const selected = recommendation.selected_strategy;

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-4 px-4 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold text-ink">Recommendation</h1>
        <p className="font-mono text-xs text-ink-3">{recommendation.recommendation_id}</p>
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
              /* Degraded is a first-class state: the recommendation still stands, and it
                 states what it was missing when it made it. */
              <StatusTag
                status="serious"
                label={recommendation.degradation_reason ?? "Produced degraded"}
              />
            ) : null}
            <Link
              href="/approvals"
              className="rounded border border-accent px-3 py-1.5 text-xs text-accent hover:bg-surface-2"
            >
              Review {approvalsFixture.cards.length} approval card
              {approvalsFixture.cards.length === 1 ? "" : "s"} →
            </Link>
          </div>
        </div>
      </Panel>

      <Worklist items={recommendation.worklist} />

      <div className="grid gap-4 xl:grid-cols-2">
        <StressMatrix
          results={recommendation.stress_results}
          replans={recommendation.replan_history}
          selectedStrategyId={selected.strategy_id}
        />
        <RejectedActions rejected={approvalsFixture.rejected} />
      </div>

      <ScenarioMatrix
        strategies={strategies}
        scores={scores}
        selectedStrategyId={selected.strategy_id}
      />
    </div>
  );
}
