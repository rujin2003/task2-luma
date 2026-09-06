import { formatMoney } from "@/lib/contracts";
import type { WorklistItem, WorklistStatus } from "@/lib/contracts";
import { AGENT_LABEL } from "@/lib/warroom";

import { Traced } from "../evidence/Traced";
import { Panel } from "../primitives";

const STATUS_LABEL: Record<WorklistStatus, string> = {
  open: "Open",
  queued: "Queued",
  needs_approval: "Needs approval",
  approved: "Approved",
  rejected: "Rejected",
  executed: "Executed",
  landed: "Landed",
  missed: "Missed",
};

const STATUS_CLASS: Record<WorklistStatus, string> = {
  open: "text-ink-2",
  queued: "text-accent",
  needs_approval: "text-warning",
  approved: "text-good",
  rejected: "text-critical",
  executed: "text-good",
  landed: "text-good",
  missed: "text-critical",
};

/**
 * The output of the whole system: rows somebody can actually work on Monday morning.
 *
 * Every column here exists because without it the row is not actionable -- an owner who
 * is not named is nobody's job, an amount with no document reference cannot be chased, and
 * a due date is the difference between a plan and a wish. The levers are proposed by
 * agents; the composition, the pricing and the ordering are deterministic code.
 */
export function Worklist({ items }: { items: WorklistItem[] }) {
  const total = items.reduce((sum, item) => sum + (item.expected_cash_impact?.minor_units ?? 0), 0);

  return (
    <Panel title="Worklist" subtitle={`${items.length} rows`}>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="border-b border-line text-ink-3">
            <tr>
              <th className="px-4 py-2 font-normal">#</th>
              <th className="px-4 py-2 font-normal">Action</th>
              <th className="px-4 py-2 font-normal">Owner</th>
              <th className="px-4 py-2 font-normal">Counterparty</th>
              <th className="px-4 py-2 font-normal">Document</th>
              <th className="px-4 py-2 text-right font-normal">Amount</th>
              <th className="px-4 py-2 text-right font-normal">Impact</th>
              <th className="px-4 py-2 font-normal">Due</th>
              <th className="px-4 py-2 font-normal">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {items.map((item) => (
              <tr key={item.seq} className="align-top">
                <td className="px-4 py-2 font-mono text-ink-3 tabular-nums">{item.seq}</td>
                <td className="max-w-[22rem] px-4 py-2 text-ink">
                  {item.action}
                  {item.proposed_by ? (
                    <span className="block text-ink-3">
                      proposed by {AGENT_LABEL[item.proposed_by]}
                    </span>
                  ) : null}
                </td>
                <td className="px-4 py-2 text-ink-2">{item.owner}</td>
                <td className="px-4 py-2 text-ink-2">{item.counterparty ?? "—"}</td>
                <td className="px-4 py-2 font-mono text-ink-2">
                  {item.evidence[0] ? (
                    <Traced reference={item.evidence[0].reference} label={item.action}>
                      {item.document_ref ?? item.evidence[0].reference}
                    </Traced>
                  ) : (
                    (item.document_ref ?? "—")
                  )}
                </td>
                <td className="px-4 py-2 text-right font-mono text-ink tabular-nums">
                  {formatMoney(item.amount, { compact: true })}
                </td>
                <td className="px-4 py-2 text-right font-mono text-ink-2 tabular-nums">
                  {item.expected_cash_impact
                    ? formatMoney(item.expected_cash_impact, { compact: true })
                    : "—"}
                  {/* A probability-weighted row says so. Never assume all open AR lands. */}
                  {item.probability_pct ? (
                    <span className="block text-ink-3">@ {item.probability_pct}%</span>
                  ) : null}
                </td>
                <td className="px-4 py-2 font-mono text-ink-2 tabular-nums">{item.due_date}</td>
                <td className={`px-4 py-2 ${STATUS_CLASS[item.status]}`}>
                  {STATUS_LABEL[item.status]}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot className="border-t border-line">
            <tr>
              <td colSpan={6} className="px-4 py-2 text-ink-3 uppercase">
                Expected impact
              </td>
              <td className="px-4 py-2 text-right font-mono text-good tabular-nums">
                {formatMoney({ minor_units: total, currency: "USD" }, { compact: true })}
              </td>
              <td colSpan={2} />
            </tr>
          </tfoot>
        </table>
      </div>
    </Panel>
  );
}
