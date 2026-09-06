"use client";

import { useEffect, useState } from "react";

import type { ApprovalRequest, Recommendation, WorklistItem } from "@/lib/contracts";
import { fetchJson } from "@/lib/api";
import { Panel } from "@/components/primitives";

function money(m: { minor_units: number; currency: string } | null | undefined): string {
  if (!m) return "—";
  const major = m.minor_units / 100;
  return `${major.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${m.currency}`;
}

export function RecommendationView() {
  const [recommendation, setRecommendation] = useState<Recommendation | null>(null);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function load() {
    try {
      const rec = await fetchJson<Recommendation>("/api/recommendation");
      setRecommendation(rec);
      const apr = await fetchJson<ApprovalRequest[]>("/api/approvals");
      setApprovals(apr);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No recommendation yet");
      setRecommendation(null);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function decide(requestId: string, approved: boolean) {
    setMessage(null);
    try {
      await fetchJson("/api/approvals/decide", {
        method: "POST",
        body: JSON.stringify({
          request_id: requestId,
          decided_by: "cfo.demo",
          approved,
          rationale: approved
            ? "Approved after reviewing stress results and rejected actions"
            : "Rejected pending further collections evidence",
          role: "cfo",
        }),
      });
      setMessage(approved ? "Approval recorded." : "Rejection recorded.");
      await load();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Decision failed");
    }
  }

  if (error && !recommendation) {
    return (
      <div className="mx-auto max-w-[1100px] px-4 py-4">
        <h1 className="text-lg font-semibold text-ink">Recommendation</h1>
        <p className="mt-3 text-sm text-ink-2">{error}</p>
        <p className="mt-1 text-sm text-ink-3">Open the War Room and run the Monday cycle first.</p>
      </div>
    );
  }

  if (!recommendation) {
    return (
      <div className="mx-auto max-w-[1100px] px-4 py-4 text-sm text-ink-3">Loading…</div>
    );
  }

  const selected = recommendation.selected_strategy;

  return (
    <div className="mx-auto flex max-w-[1200px] flex-col gap-4 px-4 py-4">
      <div>
        <h1 className="text-lg font-semibold text-ink">Recommendation</h1>
        <p className="mt-1 text-sm text-ink-2">{recommendation.summary}</p>
      </div>

      {message ? <p className="text-sm text-accent">{message}</p> : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Selected strategy" subtitle={selected.name}>
          <dl className="grid grid-cols-2 gap-2 text-xs">
            <div>
              <dt className="text-ink-3">Net cash impact</dt>
              <dd className="basis-recommendation font-mono">{money(selected.net_cash_impact)}</dd>
            </div>
            <div>
              <dt className="text-ink-3">Projected min cash</dt>
              <dd className="basis-estimate font-mono">{money(selected.projected_min_cash)}</dd>
            </div>
            <div>
              <dt className="text-ink-3">Financing cost</dt>
              <dd className="font-mono text-ink-2">{money(selected.financing_cost)}</dd>
            </div>
            <div>
              <dt className="text-ink-3">Replans</dt>
              <dd className="font-mono text-ink-2">{recommendation.replan_history.length}</dd>
            </div>
          </dl>
        </Panel>

        <Panel title="Stress results" subtitle="Calibrated to measured forecast error">
          <ul className="flex flex-col gap-2 text-xs">
            {recommendation.stress_results.map((row) => (
              <li key={`${row.strategy_id}-${row.stressor.stressor_id}`} className="flex justify-between gap-2">
                <span className="text-ink-2">{row.stressor.label}</span>
                <span className={row.passed ? "text-good" : "text-warning"}>
                  {row.passed ? "PASS" : "FAIL"} · {money(row.min_cash)}
                </span>
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <Panel title="Worklist" subtitle="Executable rows — not abstract strategies">
        <WorklistTable items={recommendation.worklist} />
      </Panel>

      <Panel title="Rejected actions" subtitle="What we did not recommend, and why">
        {recommendation.rejected_actions.length === 0 ? (
          <p className="text-xs text-ink-3">None rejected this run.</p>
        ) : (
          <ul className="flex flex-col gap-2 text-xs">
            {recommendation.rejected_actions.map((row, index) => (
              <li key={`${row.action.document_ref}-${index}`} className="border-b border-line pb-2">
                <div className="font-medium text-ink">
                  {row.action.document_ref ?? row.action.kind} · {row.rejected_by}
                </div>
                <div className="text-ink-2">{row.reason}</div>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel title="Approvals" subtitle="ACTION / AMOUNT / IMPACT / RISK / EVIDENCE / WHY / WHAT COULD GO WRONG / REQUIRED">
        <div className="flex flex-col gap-3">
          {approvals.map((request) => (
            <article key={request.request_id} className="border border-line bg-surface-0 p-3 text-xs">
              <dl className="grid gap-1 sm:grid-cols-2">
                {Object.entries(request).length > 0 ? (
                  <>
                    <div><dt className="text-ink-3">ACTION</dt><dd className="text-ink">{request.action}</dd></div>
                    <div><dt className="text-ink-3">AMOUNT</dt><dd className="font-mono">{money(request.amount)}</dd></div>
                    <div><dt className="text-ink-3">EXPECTED IMPACT</dt><dd>{request.expected_impact}</dd></div>
                    <div><dt className="text-ink-3">RISK</dt><dd>{request.risk}</dd></div>
                    <div><dt className="text-ink-3">EVIDENCE</dt><dd className="font-mono text-ink-2">{request.evidence.map((e) => e.reference).join("; ")}</dd></div>
                    <div><dt className="text-ink-3">WHY RECOMMENDED</dt><dd>{request.why_recommended}</dd></div>
                    <div><dt className="text-ink-3">WHAT COULD GO WRONG</dt><dd>{request.what_could_go_wrong}</dd></div>
                    <div><dt className="text-ink-3">APPROVAL REQUIRED</dt><dd className="uppercase">{request.approval_required}</dd></div>
                    <div><dt className="text-ink-3">STATE</dt><dd>{request.state}</dd></div>
                  </>
                ) : null}
              </dl>
              {request.state === "pending" ? (
                <div className="mt-3 flex gap-2">
                  <button
                    type="button"
                    className="border border-good px-2 py-1 text-good"
                    onClick={() => void decide(request.request_id, true)}
                  >
                    Approve
                  </button>
                  <button
                    type="button"
                    className="border border-warning px-2 py-1 text-warning"
                    onClick={() => void decide(request.request_id, false)}
                  >
                    Reject
                  </button>
                </div>
              ) : null}
            </article>
          ))}
        </div>
      </Panel>

      {recommendation.alternatives.length > 0 ? (
        <Panel title="Scenario comparison" subtitle="Side-by-side alternatives">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-ink-3">
                <tr>
                  <th className="py-1">Strategy</th>
                  <th>Impact</th>
                  <th>Min cash</th>
                  <th>Feasible</th>
                </tr>
              </thead>
              <tbody>
                {[selected, ...recommendation.alternatives].map((strategy) => (
                  <tr key={strategy.strategy_id} className="border-t border-line">
                    <td className="py-1 text-ink">{strategy.name}</td>
                    <td className="font-mono">{money(strategy.net_cash_impact)}</td>
                    <td className="font-mono">{money(strategy.projected_min_cash)}</td>
                    <td>{strategy.constraint_violations?.length ? "No" : "Yes"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : null}
    </div>
  );
}

function WorklistTable({ items }: { items: WorklistItem[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead className="text-ink-3">
          <tr>
            <th className="py-1">#</th>
            <th>Owner</th>
            <th>Action</th>
            <th>Counterparty</th>
            <th>Amount</th>
            <th>Due</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.seq} className="border-t border-line">
              <td className="py-1 font-mono">{item.seq}</td>
              <td>{item.owner}</td>
              <td className="max-w-[280px] text-ink">{item.action}</td>
              <td>{item.counterparty ?? "—"}</td>
              <td className="font-mono">{money(item.amount)}</td>
              <td className="font-mono">{item.due_date}</td>
              <td>{item.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
