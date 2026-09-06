"use client";

import type { ReactNode } from "react";

import { lookup } from "@/lib/evidence";

import { useEvidence } from "./EvidenceProvider";

/**
 * A number you can pull on.
 *
 * Three states, and the third is the one that matters:
 *
 * - **traced** -- a reference that resolves. Click or Enter opens the provenance walk.
 * - **untraced** -- no reference at all. Rendered plainly, with a title saying so, because
 *   a derived total legitimately has no single source row.
 * - **broken** -- a reference that does not resolve. Marked visibly, never silently
 *   downgraded to plain text: a number whose source has gone missing is a different thing
 *   from a number that never claimed one.
 */
export function Traced({
  reference,
  children,
  label,
  className = "",
}: {
  reference?: string;
  children: ReactNode;
  /** What this number is, for screen readers: "AR collections, week 3". */
  label?: string;
  className?: string;
}) {
  const { open } = useEvidence();

  if (!reference) {
    return (
      <span className={className} title="No single source row: this figure is derived">
        {children}
      </span>
    );
  }

  // Resolution is a question for the backend, which holds the loaded ledger, so the mark
  // here is not a verdict: the drawer resolves the reference and says plainly when the
  // chain is broken. The fixture lookup only lets the recorded demo show that state early.
  const resolves = lookup(reference)?.resolved !== false;

  return (
    <button
      type="button"
      onClick={() => open(reference)}
      title={
        resolves
          ? `${label ? `${label} · ` : ""}source ${reference} — click to trace`
          : `${reference} does not resolve to a source row`
      }
      aria-label={label ? `${label}, trace evidence` : "Trace evidence"}
      className={`cursor-pointer underline decoration-dotted decoration-ink-3 underline-offset-4 hover:decoration-accent focus-visible:outline-2 focus-visible:outline-accent ${
        resolves ? "" : "decoration-critical"
      } ${className}`}
    >
      {children}
      {resolves ? null : (
        <span className="ml-1 text-critical" aria-label="source missing">
          ✗
        </span>
      )}
    </button>
  );
}
