"use client";

import Link from "next/link";

import type { DataSource } from "@/lib/position";
import { formatMoney } from "@/lib/contracts";
import type { Money } from "@/lib/contracts";

/**
 * The empty state, used identically on every screen.
 *
 * It is a real screen rather than a spinner or a blank grid because "nothing is loaded"
 * is the correct answer most of the time, and the only useful thing to say next is where
 * to go and what will happen when you get there.
 */
export function NoSource({ what }: { what: string }) {
  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-4 px-4 py-16 text-center">
      <h1 className="text-lg font-semibold text-ink">No data source is loaded</h1>
      <p className="text-sm leading-relaxed text-ink-2">
        {what} is a view of one tenant&rsquo;s ledger, and no ledger has been loaded into this
        session yet. Load one on the Data Source screen: the DB Agent reads the schema, maps it,
        holds the mapping to the tenant&rsquo;s own trial balance and only then commits.
      </p>
      <p className="text-xs leading-relaxed text-ink-3">
        Every screen fills from that load and from the agents you run against it. Loading a
        different source clears all of it — figures never outlive the ledger they came from.
      </p>
      <div className="flex justify-center gap-3">
        <Link
          href="/onboarding"
          className="rounded border border-accent px-4 py-2 text-xs text-accent hover:bg-surface-2"
        >
          Load a data source →
        </Link>
      </div>
    </div>
  );
}

/** One line of provenance, shown at the top of every populated screen. */
export function SourceLine({ source, extra }: { source: DataSource; extra?: string }) {
  const rows = Object.entries(source.counts).reduce((total, [, count]) => total + count, 0);
  return (
    <p className="font-mono text-xs text-ink-3">
      {source.kind === "demo" ? "recorded demo ledger" : "loaded tenant"} · {source.company} · as of{" "}
      {source.as_of} · {source.currency}
      {rows > 0 ? ` · ${rows.toLocaleString("en-US")} rows` : ""}
      {extra ? ` · ${extra}` : ""}
    </p>
  );
}

export function money(value: Money | null | undefined, compact = false): string {
  if (!value) return "—";
  return formatMoney(value, { compact });
}
