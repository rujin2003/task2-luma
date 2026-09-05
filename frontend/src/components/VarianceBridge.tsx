import { formatMoney } from "@/lib/contracts";
import type { VarianceItem } from "@/lib/forecast";

import { Panel } from "./primitives";

/**
 * The question the Treasurer asks first: why did last week miss? Every material delta
 * carries a root cause and a reference the Evidence Explorer can walk down.
 */
export function VarianceBridge({ items }: { items: VarianceItem[] }) {
  const total = items.reduce((sum, item) => sum + item.delta.minor_units, 0);

  return (
    <Panel title="Variance bridge" subtitle="Prior week, plan versus actual">
      <ul className="divide-y divide-line">
        {items.map((item) => {
          const adverse = item.delta.minor_units < 0;
          return (
            <li key={item.category} className="px-4 py-3">
              <div className="flex items-baseline justify-between gap-4">
                <span className="text-sm text-ink">{item.category}</span>
                <span
                  className={`font-mono text-sm tabular-nums ${
                    adverse ? "text-critical" : "text-good"
                  }`}
                >
                  {adverse ? "✗" : "✓"} {formatMoney(item.delta, { compact: true })}
                </span>
              </div>
              <div className="mt-1 flex gap-4 font-mono text-xs text-ink-3 tabular-nums">
                <span>plan {formatMoney(item.plan, { compact: true })}</span>
                <span>actual {formatMoney(item.actual, { compact: true })}</span>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-ink-2">{item.explanation}</p>
              {item.evidence_reference ? (
                <p className="mt-1 font-mono text-xs text-ink-3">
                  {item.explained_by ? `${item.explained_by} agent · ` : ""}
                  {item.evidence_reference}
                </p>
              ) : null}
            </li>
          );
        })}
      </ul>
      <div className="flex items-baseline justify-between border-t border-line px-4 py-3">
        <span className="text-xs tracking-wide text-ink-3 uppercase">Total explained</span>
        <span
          className={`font-mono text-sm tabular-nums ${total < 0 ? "text-critical" : "text-good"}`}
        >
          {formatMoney({ minor_units: total, currency: "USD" }, { compact: true })}
        </span>
      </div>
    </Panel>
  );
}
