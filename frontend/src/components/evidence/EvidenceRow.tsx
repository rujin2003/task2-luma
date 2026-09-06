import type { EvidenceRecord } from "@/lib/evidence";
import { parseReference } from "@/lib/evidence";

/** One resolved source row, rendered the same way wherever the chain is shown. */
export function EvidenceRow({
  record,
  onWalk,
  compact = false,
}: {
  record: EvidenceRecord;
  onWalk?: (reference: string) => void;
  compact?: boolean;
}) {
  const { source, recordId, field } = parseReference(record.reference);
  const fields = Object.entries(record.fields);

  return (
    <article
      className={`rounded border border-line bg-surface-2 ${compact ? "px-3 py-2" : "px-4 py-3"}`}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-mono text-xs text-ink-2">
          <span className="text-accent">{source}</span>
          <span className="text-ink-3">:</span>
          {recordId}
          {field ? <span className="text-ink-3">#{field}</span> : null}
        </span>
        {record.as_of ? (
          <span className="font-mono text-xs text-ink-3">as of {record.as_of}</span>
        ) : null}
      </div>

      <p className={`mt-1.5 leading-relaxed text-ink ${compact ? "text-xs" : "text-sm"}`}>
        {record.excerpt}
      </p>

      {fields.length > 0 ? (
        <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-xs">
          {fields.map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="text-ink-3">{key}</dt>
              <dd className="text-ink-2 tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      {onWalk && record.derived_from.length > 0 ? (
        <button
          type="button"
          onClick={() => onWalk(record.reference)}
          className="mt-2 text-xs text-accent underline-offset-2 hover:underline"
        >
          Walk down ({record.derived_from.length} rows)
        </button>
      ) : null}
    </article>
  );
}

/**
 * A reference the ledger cannot confirm.
 *
 * This is deliberately loud. A missing row on this screen means either the fixture has a
 * gap or something cited a number nobody can trace -- and the second one is the failure
 * the whole evidence design exists to catch.
 */
export function MissingRow({
  reference,
  compact = false,
}: {
  reference: string;
  compact?: boolean;
}) {
  return (
    <article
      className={`rounded border border-critical bg-surface-2 ${
        compact ? "px-3 py-2" : "px-4 py-3"
      }`}
    >
      <p className="flex items-center gap-2 text-xs text-critical">
        <span aria-hidden>✗</span>
        <span>Does not resolve to a source row</span>
      </p>
      <p className="mt-1 font-mono text-xs break-all text-ink-3">{reference}</p>
      <p className="mt-1.5 text-xs text-ink-2">
        Nothing downstream of this reference can be relied on. A finding citing it would have been
        rejected before it reached a screen.
      </p>
    </article>
  );
}
