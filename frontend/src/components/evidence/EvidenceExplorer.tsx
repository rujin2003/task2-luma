"use client";

import { useMemo, useState } from "react";

import { allEvidence, dependents, parseReference, provenanceChain, searchEvidence } from "@/lib/evidence";

import { EvidenceRow, MissingRow } from "./EvidenceRow";
import { Panel } from "../primitives";

/**
 * Browse the ledger the way the agents cite it: by reference.
 *
 * Two panes rather than a tree. A tree looks impressive and is miserable to use when what
 * you actually have is a reference from a stack trace or a finding, and you want the row
 * it points at without navigating to it.
 */
export function EvidenceExplorer() {
  const rows = allEvidence();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(rows[0]?.reference ?? "");

  const results = useMemo(() => searchEvidence(query), [query]);
  const chain = useMemo(() => (selected ? provenanceChain(selected) : []), [selected]);
  const restsOnThis = useMemo(() => (selected ? dependents(selected) : []), [selected]);

  const root = chain[0];
  const underneath = chain.slice(1);
  const sources = new Set(rows.map((row) => parseReference(row.reference).source));

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-4 px-4 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold text-ink">Evidence Explorer</h1>
        <p className="font-mono text-xs text-ink-3">
          {rows.length} rows · {[...sources].sort().join(" · ")}
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <Panel title="References" subtitle={`${results.length} matching`}>
          <div className="border-b border-line px-4 py-3">
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search reference, excerpt or field"
              aria-label="Search evidence"
              className="w-full rounded border border-line bg-surface-2 px-3 py-2 font-mono text-xs text-ink placeholder:text-ink-3 focus-visible:outline-2 focus-visible:outline-accent"
            />
          </div>
          <ul className="max-h-[70vh] divide-y divide-line overflow-y-auto">
            {results.map((row) => {
              const { source, recordId, field } = parseReference(row.reference);
              const active = row.reference === selected;
              return (
                <li key={row.reference}>
                  <button
                    type="button"
                    onClick={() => setSelected(row.reference)}
                    aria-current={active ? "true" : undefined}
                    className={`w-full px-4 py-2.5 text-left hover:bg-surface-2 ${
                      active ? "bg-surface-2" : ""
                    }`}
                  >
                    <span className="font-mono text-xs break-all text-ink-2">
                      <span className="text-accent">{source}</span>:{recordId}
                      {field ? <span className="text-ink-3">#{field}</span> : null}
                    </span>
                    <span className="mt-1 block truncate text-xs text-ink-3">{row.excerpt}</span>
                  </button>
                </li>
              );
            })}
            {results.length === 0 ? (
              <li className="px-4 py-6 text-xs text-ink-3">
                Nothing matches. A reference with no row is either a fixture gap or a
                fabrication -- both worth knowing about.
              </li>
            ) : null}
          </ul>
        </Panel>

        <Panel title="Provenance" subtitle={selected || "nothing selected"}>
          <div className="flex flex-col gap-4 px-4 py-4">
            {root?.record ? (
              <EvidenceRow record={root.record} />
            ) : selected ? (
              <MissingRow reference={selected} />
            ) : null}

            <section>
              <h3 className="text-xs tracking-wide text-ink-3 uppercase">
                Computed from ({underneath.length})
              </h3>
              {underneath.length === 0 ? (
                <p className="mt-2 text-xs text-ink-2">
                  A source row -- the bottom of the chain.
                </p>
              ) : (
                <ul className="mt-2 flex flex-col gap-2">
                  {underneath.map((node) => (
                    <li key={node.reference} style={{ marginLeft: `${(node.depth - 1) * 12}px` }}>
                      {node.record ? (
                        <EvidenceRow record={node.record} onWalk={setSelected} compact />
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
                      <EvidenceRow record={record} onWalk={setSelected} compact />
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
          </div>
        </Panel>
      </div>
    </div>
  );
}
