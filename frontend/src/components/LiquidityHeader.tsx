import { formatMoney } from "@/lib/contracts";
import type { LiquiditySummary } from "@/lib/forecast";

import { Traced } from "./evidence/Traced";
import { StatusTag } from "./primitives";

/**
 * The liquidity summary is the Forecast screen's header, not a separate dashboard.
 * These are hero numbers -- no plot, so no hover layer, just the value and its label.
 */
function Tile({
  label,
  value,
  note,
  reference,
  emphasis = false,
}: {
  label: string;
  value: string;
  note?: React.ReactNode;
  reference?: string;
  emphasis?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1 border-l border-line px-4 first:border-l-0 first:pl-0">
      <span className="text-xs tracking-wide text-ink-3 uppercase">{label}</span>
      <Traced
        reference={reference}
        label={label}
        className={`font-mono tabular-nums ${emphasis ? "text-2xl text-ink" : "text-xl text-ink"}`}
      >
        {value}
      </Traced>
      {note ? <span className="text-xs text-ink-3">{note}</span> : null}
    </div>
  );
}

export function LiquidityHeader({ liquidity }: { liquidity: LiquiditySummary }) {
  const breached = liquidity.forecast_min_cash.minor_units < liquidity.floor.minor_units;
  const shortfall = {
    minor_units: liquidity.floor.minor_units - liquidity.forecast_min_cash.minor_units,
    currency: liquidity.floor.currency,
  };

  return (
    <div className="grid grid-cols-2 gap-y-6 rounded-lg border border-line bg-surface-1 p-4 md:grid-cols-3 lg:grid-cols-5">
      <Tile
        label="Cash today"
        value={formatMoney(liquidity.cash_today, { compact: true })}
        reference={liquidity.cash_today_reference}
        note="Bank, reconciled"
        emphasis
      />
      <Tile
        label={`Forecast minimum (W${liquidity.forecast_min_week})`}
        value={formatMoney(liquidity.forecast_min_cash, { compact: true })}
        reference={liquidity.min_cash_reference}
        note={
          breached ? (
            <StatusTag
              status="critical"
              label={`${formatMoney(shortfall, { compact: true })} below floor`}
            />
          ) : (
            <StatusTag status="good" label="Above floor" />
          )
        }
        emphasis
      />
      <Tile
        label="Policy floor"
        value={formatMoney(liquidity.floor, { compact: true })}
        reference={liquidity.floor_reference}
        note="TreasuryPolicy v4, hard constraint"
      />
      <Tile
        label="Cash runway"
        value={`${liquidity.runway_weeks} weeks`}
        note="At the current burn"
      />
      <Tile
        label="Revolver available"
        value={formatMoney(liquidity.revolver_available, { compact: true })}
        note={`${liquidity.revolver_utilization_pct}% utilised`}
      />
    </div>
  );
}
