import { formatMoney } from "@/lib/contracts";
import type { RejectedActionFixture } from "@/fixtures/warroom";

import { Traced } from "../evidence/Traced";
import { Panel } from "../primitives";

/**
 * What we did *not* recommend, and why.
 *
 * An experienced treasurer looks for this panel first, and its absence is the tell that a
 * system is optimising to look decisive. Every entry carries the lever, who refused it,
 * the reason and the rows behind the reason -- an adversarial agent that rejects a
 * proposal without attaching evidence has not made an argument, it has made an assertion.
 */
export function RejectedActions({ rejected }: { rejected: RejectedActionFixture[] }) {
  return (
    <Panel title="Rejected actions" subtitle={`${rejected.length} refused`}>
      {rejected.length === 0 ? (
        <p className="px-4 py-3 text-xs leading-relaxed text-ink-2">
          Nothing was refused. On a real week that is unusual enough to be worth checking: it
          usually means a constraint was not evaluated rather than not breached.
        </p>
      ) : (
        <ul className="divide-y divide-line">
          {rejected.map((entry, index) => (
            <li key={`${entry.action.document_ref ?? index}`} className="px-4 py-3">
              <div className="flex items-baseline justify-between gap-3">
                <p className="text-sm text-ink">
                  {entry.action.kind.replace(/_/g, " ")}
                  {entry.action.counterparty ? ` · ${entry.action.counterparty}` : ""}
                </p>
                {entry.action.amount ? (
                  <span className="font-mono text-sm text-ink-2 tabular-nums">
                    {formatMoney(entry.action.amount, { compact: true })}
                  </span>
                ) : null}
              </div>

              <p className="mt-1 text-xs leading-relaxed text-ink-3">
                {entry.action.rationale}
                {entry.action.delay_days ? ` (${entry.action.delay_days}d)` : ""}
              </p>

              <p className="mt-2 border-l-2 border-critical pl-3 text-xs leading-relaxed text-ink-2">
                <span className="text-critical">✗ refused by {entry.rejected_by}: </span>
                {entry.reason}
              </p>

              <p className="mt-1 flex flex-wrap gap-x-3 pl-3 font-mono text-xs text-ink-3">
                {entry.evidence.map((evidence) => (
                  <Traced
                    key={evidence.reference}
                    reference={evidence.reference}
                    label="Rejection evidence"
                  >
                    {evidence.reference}
                  </Traced>
                ))}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
