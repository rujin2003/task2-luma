"use client";

import type { DemoCompany } from "@/lib/onboarding";
import { formatCompact } from "@/lib/onboarding";

/**
 * The demo catalog. Four tenants, four genuinely different source schemas — an ERP, a
 * SaaS export, a legacy warehouse holding integer minor units, and a camelCase
 * application database.
 *
 * Showing the schema style on the card is the point of the screen. If every demo tenant
 * looked the same, picking one would prove nothing about the agent that reads it.
 */
export function CompanyPicker({
  companies,
  selected,
  onSelect,
  disabled,
}: {
  companies: DemoCompany[];
  selected: string | null;
  onSelect: (company: DemoCompany) => void;
  disabled: boolean;
}) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {companies.map((company) => {
        const active = company.key === selected;
        return (
          <button
            key={company.key}
            type="button"
            disabled={disabled}
            onClick={() => onSelect(company)}
            aria-pressed={active}
            className={`flex flex-col gap-2 rounded-lg border px-4 py-3 text-left transition-colors disabled:opacity-50 ${
              active
                ? "border-accent bg-surface-2"
                : "border-line bg-surface-1 hover:border-ink-3 hover:bg-surface-2"
            }`}
          >
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-sm font-semibold text-ink">{company.company}</span>
              <span className="font-mono text-[10px] tracking-wide text-ink-3 uppercase">
                {company.currency} · {company.industry}
              </span>
            </div>

            <p className="text-xs leading-relaxed text-ink-2">{company.headline}</p>

            <dl className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-3">
              <div className="flex gap-1.5">
                <dt>Schema</dt>
                <dd className="text-ink-2">{company.schema_style}</dd>
              </div>
              <div className="flex gap-1.5">
                <dt>Amounts</dt>
                <dd className="text-ink-2">
                  {company.units === "minor" ? "integer minor units" : "decimal major units"}
                </dd>
              </div>
              <div className="flex gap-1.5">
                <dt>Cash control</dt>
                <dd className="text-ink-2">
                  {formatCompact(company.control_balances.cash_control_minor, company.currency)}
                </dd>
              </div>
            </dl>

            <p className="font-mono text-[10px] break-all text-ink-3">
              {Object.entries(company.tables)
                .map(([table, count]) => `${table}(${count})`)
                .join("  ")}
            </p>
          </button>
        );
      })}
    </div>
  );
}
