import { formatMoney } from "@/lib/contracts";
import type { ForecastRow, ForecastSnapshot, ForecastWeek } from "@/lib/forecast";
import { netForWeek } from "@/lib/forecast";

import { BASIS_CLASS, BASIS_GLYPH, BASIS_LABEL, Panel } from "./primitives";
import type { CellBasis } from "@/lib/forecast";

const BASIS_ORDER: CellBasis[] = ["actual", "estimate", "assumption", "recommendation"];

export function BasisLegend() {
  return (
    <ul className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-ink-2">
      {BASIS_ORDER.map((basis) => (
        <li key={basis} className="flex items-center gap-1.5">
          <span aria-hidden className={BASIS_CLASS[basis]}>
            {BASIS_GLYPH[basis]}
          </span>
          <span className={BASIS_CLASS[basis]}>{BASIS_LABEL[basis]}</span>
        </li>
      ))}
    </ul>
  );
}

function Cell({ row, weekIndex }: { row: ForecastRow; weekIndex: number }) {
  const cell = row.cells[weekIndex];
  // Nothing scheduled is not an estimate of nothing: no amount, so no basis marking.
  if (!cell || cell.amount.minor_units === 0) {
    return <td className="px-2 py-1.5 text-right font-mono text-xs text-ink-3">—</td>;
  }

  const signed =
    row.direction === "outflow"
      ? { ...cell.amount, minor_units: -cell.amount.minor_units }
      : cell.amount;

  const title = [
    `${BASIS_LABEL[cell.basis]}: ${formatMoney(signed)}`,
    cell.reference ? `source ${cell.reference}` : null,
    cell.stale ? "driver is stale" : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <td className="px-2 py-1.5 text-right font-mono text-xs tabular-nums whitespace-nowrap">
      <span className={BASIS_CLASS[cell.basis]} title={title}>
        {formatMoney(signed, { compact: true })}
      </span>
      {cell.stale ? (
        <span className="ml-1 text-warning" title="driver is stale" aria-label="stale driver">
          ⚠
        </span>
      ) : null}
    </td>
  );
}

function WeekHeader({ week }: { week: ForecastWeek }) {
  return (
    <th
      scope="col"
      className="px-2 py-2 text-right text-xs font-medium whitespace-nowrap text-ink-3"
      title={`Week ending ${week.week_ending} · ${BASIS_LABEL[week.basis]}`}
    >
      W{week.index}
    </th>
  );
}

export function ForecastGrid({ snapshot }: { snapshot: ForecastSnapshot }) {
  const { weeks, rows, liquidity } = snapshot;
  const inflows = rows.filter((row) => row.direction === "inflow");
  const outflows = rows.filter((row) => row.direction === "outflow");

  return (
    <Panel
      title="13-week cash forecast"
      subtitle={`${snapshot.company} · version ${snapshot.forecast_version_id} · ${
        snapshot.published ? "published" : "draft"
      }`}
    >
      <div className="px-4 py-3">
        <BasisLegend />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-separate border-spacing-0 text-sm">
          <caption className="sr-only">
            Weekly cash inflows, outflows and closing balance for thirteen weeks
          </caption>
          <thead>
            <tr>
              <th
                scope="col"
                className="sticky left-0 z-10 bg-surface-1 px-4 py-2 text-left text-xs font-medium text-ink-3"
              >
                Category
              </th>
              {weeks.map((week) => (
                <WeekHeader key={week.index} week={week} />
              ))}
            </tr>
          </thead>
          <tbody>
            <SectionRows label="Inflows" rows={inflows} weekCount={weeks.length} />
            <SectionRows label="Outflows" rows={outflows} weekCount={weeks.length} />

            <tr className="border-t border-line">
              <th
                scope="row"
                className="sticky left-0 z-10 border-t border-line bg-surface-1 px-4 py-2 text-left text-xs font-medium text-ink-2"
              >
                Net movement
              </th>
              {weeks.map((week, index) => {
                const net = netForWeek(rows, index);
                return (
                  <td
                    key={week.index}
                    className="border-t border-line px-2 py-2 text-right font-mono text-xs tabular-nums whitespace-nowrap text-ink-2"
                  >
                    {formatMoney(net, { compact: true })}
                  </td>
                );
              })}
            </tr>

            <tr>
              <th
                scope="row"
                className="sticky left-0 z-10 bg-surface-1 px-4 py-2 text-left text-xs font-semibold text-ink"
              >
                Closing cash
              </th>
              {weeks.map((week) => (
                <td
                  key={week.index}
                  className="px-2 py-2 text-right font-mono text-xs tabular-nums whitespace-nowrap"
                  title={`Week ending ${week.week_ending} · ${BASIS_LABEL[week.basis]}${
                    week.breaches_floor
                      ? ` · below the ${formatMoney(liquidity.floor, { compact: true })} floor`
                      : ""
                  }`}
                >
                  <span
                    className={
                      week.breaches_floor ? "font-semibold text-critical" : BASIS_CLASS[week.basis]
                    }
                  >
                    {week.breaches_floor ? "✗ " : ""}
                    {formatMoney(week.closing_cash, { compact: true })}
                  </span>
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
      <p className="border-t border-line px-4 py-2 text-xs text-ink-3">
        ✗ marks a week below the policy floor of {formatMoney(liquidity.floor, { compact: true })}.
        Outflows are shown negative.
      </p>
    </Panel>
  );
}

function SectionRows({
  label,
  rows,
  weekCount,
}: {
  label: string;
  rows: ForecastRow[];
  weekCount: number;
}) {
  return (
    <>
      <tr>
        <th
          scope="colgroup"
          colSpan={weekCount + 1}
          className="bg-surface-2 px-4 py-1.5 text-left text-xs tracking-wide text-ink-3 uppercase"
        >
          {label}
        </th>
      </tr>
      {rows.map((row) => (
        <tr key={row.category} className="hover:bg-surface-2">
          <th
            scope="row"
            className="sticky left-0 z-10 bg-surface-1 px-4 py-1.5 text-left text-xs font-normal whitespace-nowrap text-ink-2"
          >
            {row.category}
          </th>
          {row.cells.map((_, index) => (
            <Cell key={index} row={row} weekIndex={index} />
          ))}
        </tr>
      ))}
    </>
  );
}
