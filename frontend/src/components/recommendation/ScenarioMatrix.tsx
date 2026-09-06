import type { ScoreCard } from "@/fixtures/warroom";
import { formatMoney } from "@/lib/contracts";
import type { Strategy } from "@/lib/contracts";

import { Panel } from "../primitives";

/**
 * The bundles side by side, with the trade-off each one makes stated rather than scored.
 *
 * A single number per bundle would be easier to read and would be the wrong artefact: a
 * treasurer is not choosing the highest score, they are choosing which cost to accept.
 * So the objectives are broken out with their weights and, more importantly, the sentence
 * behind each one — "utilization moves from 42.0% to 44.6% after this bundle" is a fact
 * somebody can argue with, and "covenant: 91/100" is not.
 */
export function ScenarioMatrix({
  strategies,
  scores,
  selectedStrategyId,
}: {
  strategies: Strategy[];
  scores: Record<string, ScoreCard>;
  selectedStrategyId: string;
}) {
  const objectives = [
    ...new Set(
      Object.values(scores).flatMap((card) => card.objectives.map((score) => score.objective)),
    ),
  ];

  return (
    <Panel title="Scenario comparison" subtitle={`${strategies.length} bundles`}>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="border-b border-line text-ink-3">
            <tr>
              <th className="px-4 py-2 font-normal">Objective</th>
              {strategies.map((strategy) => (
                <th key={strategy.strategy_id} className="px-4 py-2 font-normal">
                  <span
                    className={
                      strategy.strategy_id === selectedStrategyId ? "text-good" : "text-ink-2"
                    }
                  >
                    {strategy.strategy_id === selectedStrategyId ? "▲ " : ""}
                    {strategy.name}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            <tr>
              <td className="px-4 py-2 text-ink-3">Net cash impact</td>
              {strategies.map((strategy) => (
                <td
                  key={strategy.strategy_id}
                  className="px-4 py-2 font-mono text-ink tabular-nums"
                >
                  {strategy.net_cash_impact
                    ? formatMoney(strategy.net_cash_impact, { compact: true })
                    : "—"}
                </td>
              ))}
            </tr>
            <tr>
              <td className="px-4 py-2 text-ink-3">Projected min cash</td>
              {strategies.map((strategy) => (
                <td
                  key={strategy.strategy_id}
                  className="px-4 py-2 font-mono text-ink-2 tabular-nums"
                >
                  {strategy.projected_min_cash
                    ? `${formatMoney(strategy.projected_min_cash, { compact: true })} @ W${strategy.projected_min_cash_week}`
                    : "—"}
                </td>
              ))}
            </tr>
            <tr>
              <td className="px-4 py-2 text-ink-3">Financing cost</td>
              {strategies.map((strategy) => (
                <td
                  key={strategy.strategy_id}
                  className="px-4 py-2 font-mono text-ink-2 tabular-nums"
                >
                  {strategy.financing_cost
                    ? formatMoney(strategy.financing_cost, { compact: true })
                    : "—"}
                </td>
              ))}
            </tr>
            <tr>
              <td className="px-4 py-2 text-ink-3">Constraint breaches</td>
              {strategies.map((strategy) => (
                <td key={strategy.strategy_id} className="px-4 py-2">
                  {strategy.constraint_violations.length === 0 ? (
                    <span className="text-good">✓ none</span>
                  ) : (
                    <span className="text-critical">✗ {strategy.constraint_violations.length}</span>
                  )}
                </td>
              ))}
            </tr>

            {objectives.map((objective) => (
              <tr key={objective}>
                <td className="px-4 py-2 text-ink-3">{objective.replace(/_/g, " ")}</td>
                {strategies.map((strategy) => {
                  const score = scores[strategy.strategy_id]?.objectives.find(
                    (row) => row.objective === objective,
                  );
                  return (
                    <td key={strategy.strategy_id} className="max-w-[18rem] px-4 py-2">
                      {score ? (
                        <>
                          <span className="font-mono text-ink tabular-nums">
                            {Number(score.score).toFixed(0)}
                            <span className="text-ink-3"> /100 × {score.weight}</span>
                          </span>
                          <span className="mt-0.5 block leading-relaxed text-ink-3">
                            {score.basis}
                          </span>
                        </>
                      ) : (
                        <span className="text-ink-3">not scored</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
          <tfoot className="border-t border-line">
            <tr>
              <td className="px-4 py-2 text-ink-3 uppercase">Total</td>
              {strategies.map((strategy) => (
                <td
                  key={strategy.strategy_id}
                  className="px-4 py-2 font-mono text-sm text-ink tabular-nums"
                >
                  {scores[strategy.strategy_id]
                    ? Number(scores[strategy.strategy_id].total).toFixed(1)
                    : "—"}
                </td>
              ))}
            </tr>
          </tfoot>
        </table>
      </div>
    </Panel>
  );
}
