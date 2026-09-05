/**
 * The Forecast screen's view model.
 *
 * The visual grammar is the point: every number on this screen declares whether it is an
 * `actual`, an `estimate`, an `assumption` or a `recommendation`, and the UI renders the
 * four differently. A number without a basis is a bug.
 */

import type { AgentRole, Money } from "./contracts";

export type CellBasis = "actual" | "estimate" | "assumption" | "recommendation";

export interface ForecastCell {
  amount: Money;
  basis: CellBasis;
  /** Provenance reference, so the Evidence Explorer can walk down to the source row. */
  reference?: string;
  /** Set when the driver behind an assumption has gone stale. */
  stale?: boolean;
}

export interface ForecastRow {
  category: string;
  direction: "inflow" | "outflow";
  cells: ForecastCell[];
}

export interface ForecastWeek {
  index: number;
  week_ending: string;
  closing_cash: Money;
  basis: CellBasis;
  breaches_floor: boolean;
}

export interface VarianceItem {
  category: string;
  plan: Money;
  actual: Money;
  delta: Money;
  /** The Variance Agent's root cause. Empty until the agent has run. */
  explanation: string;
  explained_by?: AgentRole;
  evidence_reference?: string;
}

export interface ExceptionItem {
  id: string;
  severity: "critical" | "warning" | "info";
  headline: string;
  detail: string;
  week_index?: number;
  reference?: string;
}

export interface AccuracyPoint {
  horizon_weeks: number;
  mape_pct: string;
  sample_size: number;
}

export interface LiquiditySummary {
  cash_today: Money;
  forecast_min_cash: Money;
  forecast_min_week: number;
  floor: Money;
  runway_weeks: number;
  revolver_available: Money;
  revolver_utilization_pct: string;
}

export interface ForecastSnapshot {
  company: string;
  currency: string;
  forecast_version_id: string;
  published: boolean;
  as_of: string;
  synthetic: boolean;
  liquidity: LiquiditySummary;
  weeks: ForecastWeek[];
  rows: ForecastRow[];
  variance_bridge: VarianceItem[];
  accuracy: AccuracyPoint[];
  exceptions: ExceptionItem[];
}

export function usd(major: number): Money {
  return { minor_units: Math.round(major * 100), currency: "USD" };
}

/** Net cash movement for one week across every row. Arithmetic in code, never in a model. */
export function netForWeek(rows: ForecastRow[], weekIndex: number): Money {
  const minor_units = rows.reduce((total, row) => {
    const cell = row.cells[weekIndex];
    if (!cell) return total;
    return (
      total + (row.direction === "inflow" ? cell.amount.minor_units : -cell.amount.minor_units)
    );
  }, 0);
  return { minor_units, currency: "USD" };
}
