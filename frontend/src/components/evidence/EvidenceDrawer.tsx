"use client";

import { useEffect, useState } from "react";

import { dependents, provenanceChain, resolveLive } from "@/lib/evidence";
import type { EvidenceRecord } from "@/lib/evidence";

import { EvidenceRow, MissingRow } from "./EvidenceRow";

/**
 * The provenance walk, as a drawer over whatever screen you were on.
 *
 * The analyst clicked a number because they did not believe it. The drawer's job is to
 * answer that in one step where it can -- the source row, its fields, when it was true --
 * and to make the next step obvious where the number is an aggregate.
 *
 * A reference that does not resolve gets a row of its own saying so. Showing nothing
 * would read as "no evidence exists", when what happened is "the chain is broken here",
 * and those need different reactions from the person looking at it.
 */
export function EvidenceDrawer({
  reference,
  onClose,
  onWalk,
}: {
  reference: string | null;
  onClose: () => void;
  onWalk: (reference: string) => void;
}) {
  // One piece of state, keyed by the reference it belongs to, so switching references
  // does not need a synchronous reset inside the effect: a resolution for a reference the
  // drawer has moved on from is simply not the one being rendered.
  const [resolved, setResolved] = useState<{ reference: string; record: EvidenceRecord | null }>({
    reference: "",
    record: null,
  });

  // Resolved against the loaded source rather than looked up in a fixture: the analyst
  // clicked this number because they did not believe it, and the row that answers them is
  // the one in the tenant's own ledger.
  useEffect(() => {
    if (!reference) return;
    resolveLive(reference)
      .then((row) => setResolved({ reference, record: row.resolved ? row : null }))
      .catch(() => setResolved({ reference, record: null }));
  }, [reference]);

  useEffect(() => {
    if (!reference) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [reference, onClose]);

  if (!reference) return null;

  const chain = provenanceChain(reference);
  const settled = resolved.reference === reference;
  const live = settled ? resolved.record : null;
  const root = live ? { reference, record: live, depth: 0 } : chain[0];
  const underneath = chain.slice(1);
  const restsOnThis = dependents(reference);

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-label="Evidence">
      <button
        type="button"
        aria-label="Close evidence"
        onClick={onClose}
        className="flex-1 cursor-default bg-surface-0/70"
      />
      <aside className="flex h-full w-full max-w-xl flex-col overflow-y-auto border-l border-line bg-surface-1 shadow-2xl">
        <header className="sticky top-0 flex items-start justify-between gap-4 border-b border-line bg-surface-1 px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold tracking-wide text-ink uppercase">Evidence</h2>
            <p className="mt-1 font-mono text-xs break-all text-ink-3">{reference}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-line px-2 py-1 text-xs text-ink-2 hover:text-ink"
          >
            Close (Esc)
          </button>
        </header>

        <div className="flex flex-col gap-4 px-4 py-4">
          {root?.record ? (
            <EvidenceRow record={root.record} />
          ) : settled ? (
            <MissingRow reference={reference} />
          ) : (
            <p className="text-xs text-ink-3">Resolving against the loaded ledger…</p>
          )}

          <section>
            <h3 className="text-xs tracking-wide text-ink-3 uppercase">
              Computed from ({underneath.length})
            </h3>
            {underneath.length === 0 ? (
              <p className="mt-2 text-xs text-ink-2">
                A source row. This is the bottom of the chain -- the ledger itself.
              </p>
            ) : (
              <ul className="mt-2 flex flex-col gap-2">
                {underneath.map((node) => (
                  <li key={node.reference} style={{ marginLeft: `${(node.depth - 1) * 12}px` }}>
                    {node.record ? (
                      <EvidenceRow record={node.record} onWalk={onWalk} compact />
                    ) : (
                      <MissingRow reference={node.reference} compact />
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>

          {restsOnThis.length > 0 ? (
            <section>
              <h3 className="text-xs tracking-wide text-ink-3 uppercase">
                What rests on this ({restsOnThis.length})
              </h3>
              <ul className="mt-2 flex flex-col gap-2">
                {restsOnThis.map((record) => (
                  <li key={record.reference}>
                    <EvidenceRow record={record} onWalk={onWalk} compact />
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          <p className="border-t border-line pt-3 text-xs text-ink-3">
            Provenance is `(source, record, field, as of)`, resolved against the source that is
            currently loaded. A reference that no longer resolves is drawn as a break in the chain
            rather than quietly omitted.
          </p>
        </div>
      </aside>
    </div>
  );
}
