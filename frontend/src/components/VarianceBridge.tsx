import { formatMoney } from "@/lib/contracts";
import type { VarianceItem } from "@/lib/forecast";

import { Traced } from "./evidence/Traced";
import { Panel, StatusTag } from "./primitives";

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
                <Traced
                  reference={item.evidence_reference}
                  label={`${item.category} variance`}
                  className={`font-mono text-sm tabular-nums ${
                    adverse ? "text-critical" : "text-good"
                  }`}
                >
                  {adverse ? "✗" : "✓"} {formatMoney(item.delta, { compact: true })}
                </Traced>
              </div>
              <div className="mt-1 flex gap-4 font-mono text-xs text-ink-3 tabular-nums">
                <span>plan {formatMoney(item.plan, { compact: true })}</span>
                <span>actual {formatMoney(item.actual, { compact: true })}</span>
              </div>
              {item.explanation ? (
                <p className="mt-2 text-xs leading-relaxed text-ink-2">{item.explanation}</p>
              ) : (
                /* Degraded is a state of this row, not an error toast over the screen. */
                <div className="mt-2 flex flex-col gap-1">
                  <StatusTag status="warning" label="Unexplained" />
                  <p className="text-xs leading-relaxed text-ink-3">
                    {item.unexplained_reason ?? "No agent has explained this delta yet."}
                  </p>
                </div>
              )}
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
        <span
          className="text-xs tracking-wide text-ink-3 uppercase"
          title="Sum of the rows above; no single source row"
        >
          Bridge total
        </span>
        <span
          className={`font-mono text-sm tabular-nums ${total < 0 ? "text-critical" : "text-good"}`}
        >
          {formatMoney({ minor_units: total, currency: "USD" }, { compact: true })}
        </span>
      </div>
    </Panel>
  );
}
