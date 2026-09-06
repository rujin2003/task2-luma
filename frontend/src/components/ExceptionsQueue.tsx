import type { ExceptionItem } from "@/lib/forecast";

import { Traced } from "./evidence/Traced";
import { Panel, StatusTag } from "./primitives";

const SEVERITY_LABEL: Record<ExceptionItem["severity"], string> = {
  critical: "Critical",
  warning: "Review",
  info: "Note",
};

const SEVERITY_STATUS = {
  critical: "critical",
  warning: "warning",
  info: "good",
} as const;

/**
 * Only what moved materially or where an assumption went stale. A queue that lists
 * everything is a queue nobody reads on a Monday.
 */
export function ExceptionsQueue({ exceptions }: { exceptions: ExceptionItem[] }) {
  return (
    <Panel title="Exceptions" subtitle={`${exceptions.length} open`}>
      <ul className="divide-y divide-line">
        {exceptions.map((exception) => (
          <li key={exception.id} className="px-4 py-3">
            <div className="flex items-start justify-between gap-3">
              <p className="text-sm text-ink">{exception.headline}</p>
              <StatusTag
                status={SEVERITY_STATUS[exception.severity]}
                label={SEVERITY_LABEL[exception.severity]}
              />
            </div>
            <p className="mt-1 text-xs leading-relaxed text-ink-2">{exception.detail}</p>
            {exception.reference ? (
              <p className="mt-1 font-mono text-xs text-ink-3">
                {exception.week_index ? `W${exception.week_index} · ` : ""}
                <Traced reference={exception.reference} label={exception.headline}>
                  {exception.reference}
                </Traced>
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </Panel>
  );
}
