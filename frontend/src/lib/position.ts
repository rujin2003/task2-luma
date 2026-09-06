/**
 * The data source, and the position derived from it. Mirrors `backend/api/datasource.py`.
 *
 * Every screen in this product is a view of one data source. `source === null` is not an
 * error state and not a loading state — it is the honest answer that nothing has been
 * loaded, and each screen renders `<NoSource />` rather than an empty grid that looks
 * like a bug.
 */

import type { Money } from "@/lib/contracts";
import { fetchJson } from "@/lib/api";

export interface DataSource {
  kind: "tenant" | "demo";
  tenant_id: string;
  company: string;
  currency: string;
  as_of: string;
  loaded_at: string;
  counts: Record<string, number>;
  mapping_path: string | null;
  reconciled: boolean | null;
  available_sources: string[];
  missing_sources: string[];
  source_notes: Record<string, string>;
  note: string;
}

export interface DataSourceStatus {
  source: DataSource | null;
  company: string | null;
  as_of: string | null;
  provider: string;
  has_cycle: boolean;
  has_investigation: boolean;
  has_recommendation: boolean;
  agent_runs: number;
}

export interface ForecastWeek {
  week_index: number;
  week_ending: string;
  closing_cash: Money;
  breaches_floor: boolean;
}

export interface Position {
  source: DataSource;
  liquidity: {
    cash_today: Money;
    floor: Money;
    min_cash: Money;
    min_cash_week: number;
    revolver_available: Money;
    revolver_utilization_pct: string;
    runway_weeks: number;
    references: string[];
  } | null;
  forecast: { version_id: string; published: boolean; weeks: ForecastWeek[] } | null;
  aging: {
    buckets: { label: string; amount: Money; invoice_count: number }[];
    total: Money;
    references: string[];
  } | null;
  assumptions: {
    rows: {
      category: string;
      driver: string;
      value_display: string;
      last_refreshed: string;
      days_since_refresh: number;
      stale: boolean;
      reference: string;
    }[];
  } | null;
  constraints: {
    constraints: {
      constraint_id: string;
      kind: string;
      severity: string;
      description: string;
      money_threshold: Money | null;
      applies_to: string | null;
      source_ref: string | null;
    }[];
  } | null;
  capabilities: {
    available_sources: string[];
    missing_sources: string[];
    notes: Record<string, string>;
  } | null;
  /** Tool name → the reason this tenant cannot answer it. Rendered, never swallowed. */
  unavailable: Record<string, string>;
}

export function getDataSource(): Promise<DataSourceStatus> {
  return fetchJson<DataSourceStatus>("/api/data-source");
}

export function getPosition(): Promise<Position> {
  return fetchJson<Position>("/api/data-source/position");
}

/** A 409 from any screen means "nothing loaded", which is a state, not a failure. */
export function isNoSource(error: unknown): boolean {
  return error instanceof Error && error.message.startsWith("409");
}
