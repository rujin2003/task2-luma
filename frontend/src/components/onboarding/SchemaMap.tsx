"use client";

import { Panel } from "@/components/primitives";
import type { ClassifyResult, IntrospectResult } from "@/lib/onboarding";
import { bps } from "@/lib/onboarding";

/**
 * What the agent decided, table by table, with the confidence it decided it at.
 *
 * This is the screen's centre of gravity. A POC pasting a database URL is not asking
 * to be told "mapping complete" — they are asking whether the thing that read their
 * schema understood it, and the only honest way to answer is to show the mapping and
 * let them disagree with a row. So every column that was matched is listed with its
 * confidence, and everything the lexicon could *not* place is listed too.
 */
export function SchemaMap({
  introspection,
  classification,
}: {
  introspection: IntrospectResult;
  classification: ClassifyResult;
}) {
  const byTable = new Map(classification.tables.map((row) => [row.table, row]));
  const unitsByColumn = new Map(
    classification.units.map((unit) => [`${unit.table}.${unit.column}`, unit.units]),
  );

  return (
    <Panel
      title="Schema map"
      subtitle={`${classification.tables.length} of ${introspection.tables.length} tables classified · ${
        classification.model_calls === 0
          ? "no model call"
          : `${classification.model_calls} model call`
      }`}
    >
      <div className="divide-y divide-line">
        {introspection.tables.map((table) => {
          const verdict = byTable.get(table.name);
          const mapped = classification.columns.filter((column) => column.table === table.name);
          const mappedNames = new Set(mapped.map((column) => column.column));

          return (
            <div key={table.name} className="px-4 py-3">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="font-mono text-xs text-ink">{table.name}</span>
                <span className="font-mono text-[10px] text-ink-3">
                  {table.row_count.toLocaleString("en-US")} rows
                </span>
                {verdict ? (
                  <span className="text-xs text-good">
                    → {verdict.entity_role}{" "}
                    <span className="font-mono text-[10px] text-ink-3">
                      {bps(verdict.confidence_bps)}
                    </span>
                  </span>
                ) : (
                  <span className="text-xs text-ink-3">unclassified — not one of our entities</span>
                )}
              </div>

              {mapped.length > 0 ? (
                <div className="mt-2 overflow-x-auto">
                  <table className="w-full min-w-[520px] border-collapse text-left">
                    <thead>
                      <tr className="text-[10px] tracking-wide text-ink-3 uppercase">
                        <th className="py-1 pr-4 font-normal">Source column</th>
                        <th className="py-1 pr-4 font-normal">Type</th>
                        <th className="py-1 pr-4 font-normal">Canonical field</th>
                        <th className="py-1 pr-4 font-normal">Units</th>
                        <th className="py-1 font-normal">Confidence</th>
                      </tr>
                    </thead>
                    <tbody className="align-top">
                      {mapped.map((column) => {
                        const source = table.columns.find((item) => item.name === column.column);
                        const units = unitsByColumn.get(`${table.name}.${column.column}`);
                        return (
                          <tr key={column.column} className="border-t border-line/60">
                            <td className="py-1 pr-4 font-mono text-xs text-ink-2">
                              {column.column}
                            </td>
                            <td className="py-1 pr-4 font-mono text-[10px] text-ink-3">
                              {source?.data_type ?? "—"}
                            </td>
                            <td className="py-1 pr-4 font-mono text-xs text-ink">
                              {column.canonical_field}
                            </td>
                            <td className="py-1 pr-4 font-mono text-[10px] text-ink-3">
                              {units ?? "—"}
                            </td>
                            <td className="py-1 font-mono text-[10px] text-ink-3">
                              {bps(column.confidence_bps)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : null}

              {verdict && table.columns.some((column) => !mappedNames.has(column.name)) ? (
                <p className="mt-2 text-[11px] leading-snug text-ink-3">
                  Not mapped:{" "}
                  <span className="font-mono">
                    {table.columns
                      .filter((column) => !mappedNames.has(column.name))
                      .map((column) => column.name)
                      .join(", ")}
                  </span>
                </p>
              ) : null}
            </div>
          );
        })}
      </div>

      {classification.residue.tables.length > 0 || classification.residue.columns.length > 0 ? (
        <div className="border-t border-line px-4 py-3">
          <h3 className="text-[10px] tracking-wide text-ink-3 uppercase">Residue</h3>
          <p className="mt-1 text-[11px] leading-relaxed text-ink-2">
            What the lexicon could not resolve. This — and only this — is what a model would be
            shown, as column names and types with no values attached.
          </p>
          {classification.residue.tables.length > 0 ? (
            <p className="mt-1 font-mono text-[11px] text-ink-3">
              tables: {classification.residue.tables.join(", ")}
            </p>
          ) : null}
          {classification.residue.columns.length > 0 ? (
            <p className="mt-1 font-mono text-[11px] text-ink-3">
              fields: {classification.residue.columns.map((pair) => pair.join(".")).join(", ")}
            </p>
          ) : null}
        </div>
      ) : null}
    </Panel>
  );
}
