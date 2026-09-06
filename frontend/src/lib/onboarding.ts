/**
 * Types and helpers for the DB Agent screen.
 *
 * These mirror `backend/api/onboarding.py`. They are hand-written rather than generated
 * because the screen consumes a deliberately narrow slice of each response, and a
 * generated client would tempt it into rendering fields the design has no place for.
 */

export const STAGES = [
  "connect",
  "introspect",
  "classify",
  "map",
  "reconcile",
  "freeze",
  "load",
  "drift",
] as const;

export type Stage = (typeof STAGES)[number];
export type StageState = "pending" | "ok" | "failed";

export const STAGE_LABEL: Record<Stage, string> = {
  connect: "Connect",
  introspect: "Read schema",
  classify: "Classify",
  map: "Draft mapping",
  reconcile: "Reconcile",
  freeze: "Freeze",
  load: "Load",
  drift: "Drift watch",
};

export const STAGE_NOTE: Record<Stage, string> = {
  connect: "Test the credential; report read and write capability.",
  introspect: "Names, types, keys, row counts. No financial value is read.",
  classify: "Match each table to an entity and each column to a canonical field.",
  map: "Turn the proposal into a declarative mapping, units and all.",
  reconcile: "Hold the mapping to the tenant's own control totals.",
  freeze: "Write mapping.v1.yaml. After this, ingestion is deterministic code.",
  load: "Read the source rows; write ours. Rejected rows are reported, not dropped.",
  drift: "Re-hash the schema. A change holds ingestion rather than corrupting it.",
};

export interface DemoCompany {
  key: string;
  company: string;
  industry: string;
  headline: string;
  schema_style: string;
  units: "major" | "minor";
  currency: string;
  url: string;
  as_of: string;
  tables: Record<string, number>;
  control_balances: {
    as_of: string;
    ar_control_minor: number;
    ap_control_minor: number;
    cash_control_minor: number;
    currency: string;
  };
}

export interface CompanyCatalog {
  available: boolean;
  hint: string | null;
  as_of?: string;
  companies: DemoCompany[];
}

export interface Probe {
  dialect: string;
  server_version: string;
  schema: string;
  can_read: boolean;
  can_write: boolean;
  write_note: string;
  table_count: number;
}

export interface OnboardingStatus {
  connected: boolean;
  state: Record<string, StageState>;
  summary: { source?: string; company?: string; schema?: string | null } | null;
  fingerprint_hash: string | null;
  table_count: number;
  entities: string[];
  mapping_path: string | null;
  reconciled: boolean | null;
  reconciliation_failures: string[];
  counts: Record<string, number>;
}

export interface SchemaColumn {
  name: string;
  data_type: string;
  nullable: boolean;
  is_pk: boolean;
  is_fk: boolean;
  fk_target: string | null;
  format_signature: string | null;
}

export interface SchemaTable {
  name: string;
  row_count: number;
  columns: SchemaColumn[];
}

export interface IntrospectResult {
  fingerprint_hash: string;
  dialect: string;
  tables: SchemaTable[];
  status: OnboardingStatus;
}

export interface ClassifyResult {
  template: { template_id: string; score_bps: number } | null;
  model_calls: number;
  tables: { table: string; entity_role: string; confidence_bps: number }[];
  columns: {
    table: string;
    column: string;
    canonical_field: string;
    confidence_bps: number;
  }[];
  units: { table: string; column: string; units: string; currency_source: string }[];
  residue: { tables: string[]; columns: string[][] };
  status: OnboardingStatus;
}

export interface LoadResult {
  accepted: boolean;
  committed: boolean;
  counts: Record<string, number>;
  source_rows: number;
  mapped_rows: number;
  assumptions: string[];
  rejects: { entity: string; source_key: string; reason: string }[];
  reject_count: number;
  totals: {
    open_invoices_minor: number;
    open_vendor_invoices_minor: number;
    bank_transactions_minor: number;
    currency: string;
  };
  reconciliation: {
    accepted: boolean;
    coverage_rows_bps: number;
    coverage_value_bps: number;
    failures: string[];
  } | null;
  mapping_path: string | null;
  status: OnboardingStatus;
}

export interface Ledger {
  tenant_id: string;
  company: string;
  currency: string;
  counts: Record<string, number>;
  totals: { open_ar_minor: number; open_ap_minor: number; cash_minor: number };
  largest_open_receivables: {
    invoice_ref: string;
    customer: string;
    open_minor: number;
    currency: string;
    due_date: string;
    days_past_due: number;
  }[];
}

/** Minor units to a display string. Integer arithmetic only — this is money. */
export function formatMinor(minor: number, currency = "USD"): string {
  const negative = minor < 0;
  const value = Math.abs(minor);
  const whole = Math.trunc(value / 100).toLocaleString("en-US");
  const cents = String(value % 100).padStart(2, "0");
  return `${negative ? "-" : ""}${currency} ${whole}.${cents}`;
}

/** Compact form for headline figures: `USD 23.8M`. */
export function formatCompact(minor: number, currency = "USD"): string {
  const negative = minor < 0;
  const major = Math.abs(Math.trunc(minor / 100));
  const [scale, suffix] =
    major >= 1_000_000 ? [1_000_000, "M"] : major >= 1_000 ? [1_000, "K"] : [1, ""];
  const scaled = major / scale;
  const text = suffix ? scaled.toFixed(scaled >= 100 ? 0 : 1) : String(major);
  return `${negative ? "-" : ""}${currency} ${text}${suffix}`;
}

export function bps(value: number): string {
  return `${(value / 100).toFixed(value % 100 === 0 ? 0 : 1)}%`;
}
