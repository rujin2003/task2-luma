import type { ReactNode } from "react";

import type { CellBasis } from "@/lib/forecast";

export const BASIS_LABEL: Record<CellBasis, string> = {
  actual: "Actual",
  estimate: "Estimate",
  assumption: "Assumption",
  recommendation: "Recommendation",
};

export const BASIS_CLASS: Record<CellBasis, string> = {
  actual: "basis-actual",
  estimate: "basis-estimate",
  assumption: "basis-assumption",
  recommendation: "basis-recommendation",
};

/** The glyph half of the grammar: meaning survives greyscale and forced colours. */
export const BASIS_GLYPH: Record<CellBasis, string> = {
  actual: "●",
  estimate: "◐",
  assumption: "○",
  recommendation: "▲",
};

export type Status = "good" | "warning" | "serious" | "critical";

export const STATUS_CLASS: Record<Status, string> = {
  good: "text-good",
  warning: "text-warning",
  serious: "text-serious",
  critical: "text-critical",
};

export const STATUS_GLYPH: Record<Status, string> = {
  good: "✓",
  warning: "⚠",
  serious: "▲",
  critical: "✗",
};

export function Panel({
  title,
  subtitle,
  children,
  className = "",
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-lg border border-line bg-surface-1 ${className}`}
      aria-label={title}
    >
      <header className="flex items-baseline justify-between gap-4 border-b border-line px-4 py-3">
        <h2 className="text-sm font-semibold tracking-wide text-ink uppercase">{title}</h2>
        {subtitle ? <p className="text-xs text-ink-3">{subtitle}</p> : null}
      </header>
      {children}
    </section>
  );
}

/** A status never travels without its glyph and its word. */
export function StatusTag({ status, label }: { status: Status; label: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs ${STATUS_CLASS[status]}`}>
      <span aria-hidden>{STATUS_GLYPH[status]}</span>
      <span>{label}</span>
    </span>
  );
}
