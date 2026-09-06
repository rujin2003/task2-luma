"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { NoSource, SourceLine } from "@/components/SourceBanner";
import { ApprovalCard } from "@/components/approvals/ApprovalCard";
import { Panel } from "@/components/primitives";
import { RejectedActions } from "@/components/recommendation/RejectedActions";
import { Worklist } from "@/components/recommendation/Worklist";
import type { ApprovalCardFixture, RejectedActionFixture } from "@/fixtures/warroom";
import { fetchJson } from "@/lib/api";
import type { WorklistItem } from "@/lib/contracts";
import { getDataSource, type DataSourceStatus } from "@/lib/position";

interface Board {
  field_order: string[];
  worklist: WorklistItem[];
  cards: ApprovalCardFixture[];
  rejected: RejectedActionFixture[];
}

/**
 * The signing surface for the loaded tenant: every row that needs a human, and every row
 * that does not.
 *
 * Signing here is real in every respect except the money: the decision goes through
 * maker-checker, the delegation matrix and the blocked-route rules, and lands in the
 * append-only audit log with the card exactly as it was rendered. Execution is a
 * dry-run adapter by design — nothing in this build moves cash.
 */
export default function ApprovalsScreen() {
  const [status, setStatus] = useState<DataSourceStatus | null>(null);
  const [board, setBoard] = useState<Board | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [signer, setSigner] = useState("cfo@warroom");
  const [role, setRole] = useState("cfo");

  const load = useCallback(async () => {
    const next = await getDataSource();
    setStatus(next);
    if (!next.has_recommendation) return;
    try {
      setBoard(await fetchJson<Board>("/api/approvals"));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    getDataSource()
      .then(async (next) => {
        setStatus(next);
        if (next.has_recommendation) setBoard(await fetchJson<Board>("/api/approvals"));
      })
      .catch((cause: Error) => setError(cause.message));
  }, []);

  async function decide(requestId: string, approved: boolean) {
    setBusy(requestId);
    setError(null);
    try {
      await fetchJson(`/api/approvals/${requestId}/decide`, {
        method: "POST",
        body: JSON.stringify({
          decided_by: signer,
          decided_by_role: role,
          approved,
          reason: approved ? "Approved in the console" : "Declined in the console",
        }),
      });
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  if (status && status.source === null) return <NoSource what="Approvals" />;
  if (!status) return <p className="px-4 py-8 text-xs text-ink-3">Loading…</p>;

  if (!board) {
    return (
      <div className="mx-auto flex max-w-2xl flex-col gap-4 px-4 py-16 text-center">
        <h1 className="text-lg font-semibold text-ink">Nothing to approve</h1>
        <p className="text-sm leading-relaxed text-ink-2">
          Approval cards are prepared from a recommendation, and no investigation has produced one
          for {status.source?.company} yet.
        </p>
        {error ? <p className="text-xs text-critical">✗ {error}</p> : null}
        <div className="flex justify-center">
          <Link
            href="/war-room"
            className="rounded border border-accent px-4 py-2 text-xs text-accent hover:bg-surface-2"
          >
            Open the War Room →
          </Link>
        </div>
      </div>
    );
  }

  const gated = board.cards.filter((card) => card.state !== "blocked");
  const blocked = board.cards.filter((card) => card.state === "blocked");
  const autoSafe = board.worklist.filter((row) => row.status === "queued");

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-4 px-4 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold text-ink">Approvals</h1>
        {status.source ? (
          <SourceLine
            source={status.source}
            extra={`${gated.length} awaiting · ${autoSafe.length} auto-safe · ${blocked.length} blocked`}
          />
        ) : null}
      </div>

      {error ? (
        <p className="rounded-lg border border-critical bg-surface-1 px-4 py-3 text-xs leading-relaxed text-critical">
          ✗ {error}
        </p>
      ) : null}

      <Panel title="Execution mode" subtitle="dry run">
        <div className="flex flex-wrap items-end gap-4 px-4 py-3">
          <p className="max-w-2xl flex-1 text-xs leading-relaxed text-ink-2">
            Approving records the decision and the card that was signed, and moves no money. Nothing
            executes without a recorded, matching, maker-checker-clean approval, and the preparer of
            a card can never be the person who signs it — a decision from{" "}
            <span className="font-mono">analyst@novatech</span> is refused here, not ignored.
          </p>
          <label className="flex flex-col gap-1 text-[11px] text-ink-3">
            Signing as
            <input
              value={signer}
              onChange={(event) => setSigner(event.target.value)}
              className="w-56 rounded border border-line bg-surface-2 px-3 py-1.5 font-mono text-xs text-ink"
            />
          </label>
          <label className="flex flex-col gap-1 text-[11px] text-ink-3">
            Role
            <select
              value={role}
              onChange={(event) => setRole(event.target.value)}
              className="rounded border border-line bg-surface-2 px-3 py-1.5 text-xs text-ink"
            >
              <option value="analyst">analyst</option>
              <option value="treasurer">treasurer</option>
              <option value="cfo">cfo</option>
              <option value="board">board</option>
            </select>
          </label>
        </div>
      </Panel>

      <div className="grid gap-4 xl:grid-cols-2">
        {gated.map((card) => (
          <div key={card.request.request_id} className="flex flex-col gap-2">
            <ApprovalCard card={card} />
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={() => decide(card.request.request_id, true)}
                disabled={busy !== null || card.state === "approved"}
                className="rounded border border-good px-3 py-1.5 text-xs text-good hover:bg-surface-2 disabled:border-line disabled:text-ink-3"
              >
                {card.state === "approved" ? "Approved" : "Approve"}
              </button>
              <button
                type="button"
                onClick={() => decide(card.request.request_id, false)}
                disabled={busy !== null || card.state === "rejected"}
                className="rounded border border-critical px-3 py-1.5 text-xs text-critical hover:bg-surface-2 disabled:border-line disabled:text-ink-3"
              >
                {card.state === "rejected" ? "Rejected" : "Decline"}
              </button>
              <span className="text-[11px] text-ink-3">
                {card.state === "pending" ? "awaiting a decision" : card.state}
              </span>
            </div>
          </div>
        ))}
      </div>

      {blocked.length > 0 ? (
        <>
          <h2 className="mt-2 text-sm font-semibold tracking-wide text-critical uppercase">
            Blocked by policy
          </h2>
          <div className="grid gap-4 xl:grid-cols-2">
            {blocked.map((card) => (
              <ApprovalCard key={card.request.request_id} card={card} />
            ))}
          </div>
        </>
      ) : null}

      <Worklist items={board.worklist} />

      <RejectedActions rejected={board.rejected} />
    </div>
  );
}
