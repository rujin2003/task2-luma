import type { AccuracyPoint } from "@/lib/forecast";

import { Panel } from "./primitives";

/**
 * Measured forecast error by horizon. One series, so no legend -- the title names it and
 * every bar is directly labelled. This is also what calibrates the stress test: "AR at
 * the 90th percentile of our own error", never an arbitrary shock.
 */
export function AccuracyPanel({ points }: { points: AccuracyPoint[] }) {
  const worst = Math.max(...points.map((point) => Number(point.mape_pct)), 1);

  return (
    <Panel title="Forecast accuracy" subtitle="MAPE by horizon, 26-week roll-forward">
      <ul className="flex flex-col gap-3 px-4 py-4">
        {points.map((point) => {
          const value = Number(point.mape_pct);
          return (
            <li key={point.horizon_weeks} className="flex items-center gap-3">
              <span className="w-10 shrink-0 font-mono text-xs text-ink-3 tabular-nums">
                W{point.horizon_weeks}
              </span>
              <div className="h-2 flex-1 rounded-sm bg-surface-2">
                <div
                  className="h-2 rounded-r-sm bg-accent"
                  style={{ width: `${Math.max((value / worst) * 100, 2)}%` }}
                />
              </div>
              <span className="w-24 shrink-0 text-right font-mono text-xs text-ink tabular-nums">
                {point.mape_pct}%<span className="ml-1 text-ink-3">n={point.sample_size}</span>
              </span>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}
