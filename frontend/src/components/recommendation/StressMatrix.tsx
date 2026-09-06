import { formatMoney } from "@/lib/contracts";
import type { ReplanAttempt, StressResult } from "@/lib/contracts";

import { Panel, StatusTag } from "../primitives";

/**
 * Every bundle against every stressor, including the bundles that failed.
 *
 * Two things this panel is built to make impossible to miss.
 *
 * The first is the calibration. Each stressor states where its size came from, and on this
 * data that is "the 90th percentile of this company's own W6 error over 26 weeks", not a
 * round number somebody liked. A −10% shock is a number; a −17.3% shock measured from your
 * own forecast error is an argument.
 *
 * The second is the failures. A bundle that failed stress is not removed from the matrix
 * and replaced quietly -- it stays, with its shortfall, next to the replan that followed
 * it. "We tried the cheaper plan and it did not survive an AR shortfall at our own 90th
 * percentile" is the most persuasive sentence in the output, and it only exists if the
 * failed row is still on screen.
 */
export function StressMatrix({
  results,
  replans,
  selectedStrategyId,
}: {
  results: StressResult[];
  replans: ReplanAttempt[];
  selectedStrategyId: string;
}) {
  const strategies = [...new Set(results.map((result) => result.strategy_id))];
  const stressors = [...new Map(results.map((r) => [r.stressor.stressor_id, r.stressor])).values()];
  const at = (strategyId: string, stressorId: string) =>
    results.find((r) => r.strategy_id === strategyId && r.stressor.stressor_id === stressorId);

  return (
    <Panel
      title="Stress results"
      subtitle={`${strategies.length} bundles × ${stressors.length} stressors`}
    >
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="border-b border-line text-ink-3">
            <tr>
              <th className="px-4 py-2 font-normal">Bundle</th>
              {stressors.map((stressor) => (
                <th key={stressor.stressor_id} className="px-4 py-2 font-normal">
                  {stressor.label}
                  <span className="block font-mono text-ink-3 tabular-nums">
                    {stressor.shift_pct}%
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {strategies.map((strategyId) => (
              <tr key={strategyId}>
                <td className="px-4 py-2 text-ink">
                  {strategyId}
                  {strategyId === selectedStrategyId ? (
                    <span className="ml-2 text-good">▲ selected</span>
                  ) : null}
                </td>
                {stressors.map((stressor) => {
                  const cell = at(strategyId, stressor.stressor_id);
                  if (!cell) {
                    return (
                      <td key={stressor.stressor_id} className="px-4 py-2 text-ink-3">
                        not tested
                      </td>
                    );
                  }
                  return (
                    <td key={stressor.stressor_id} className="px-4 py-2">
                      <StatusTag
                        status={cell.passed ? "good" : "critical"}
                        label={cell.passed ? "Holds" : "Breaks"}
                      />
                      <span
                        className={`block font-mono tabular-nums ${
                          cell.passed ? "text-ink-2" : "text-critical"
                        }`}
                      >
                        {formatMoney(cell.min_cash, { compact: true })} @ W{cell.min_cash_week}
                      </span>
                      <span className="block font-mono text-ink-3 tabular-nums">
                        headroom {formatMoney(cell.headroom, { compact: true })}
                      </span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="border-t border-line px-4 py-3">
        <p className="text-xs text-ink-3 uppercase">Calibration</p>
        <ul className="mt-1 space-y-1">
          {stressors.map((stressor) => (
            <li key={stressor.stressor_id} className="text-xs leading-relaxed text-ink-2">
              <span className="text-ink">{stressor.label}</span> — {stressor.calibration}
            </li>
          ))}
        </ul>
      </div>

      {replans.length > 0 ? (
        <div className="border-t border-line px-4 py-3">
          <p className="text-xs text-ink-3 uppercase">Replans</p>
          <ol className="mt-1 space-y-1.5">
            {replans.map((attempt) => (
              <li key={attempt.attempt} className="text-xs leading-relaxed text-ink-2">
                <span className="text-warning">
                  ↻ attempt {attempt.attempt} · {attempt.strategy_id}
                </span>{" "}
                — {attempt.failure_reason}
                {attempt.tightened_constraint ? (
                  <span className="block text-ink-3">
                    tightened: {attempt.tightened_constraint}
                  </span>
                ) : null}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </Panel>
  );
}
