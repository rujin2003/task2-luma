"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { CompanyPicker } from "@/components/onboarding/CompanyPicker";
import { SchemaMap } from "@/components/onboarding/SchemaMap";
import { StageRail } from "@/components/onboarding/StageRail";
import { Panel } from "@/components/primitives";
import { fetchJson } from "@/lib/api";
import {
  STAGES,
  formatMinor,
  type ClassifyResult,
  type CompanyCatalog,
  type DemoCompany,
  type IntrospectResult,
  type Ledger,
  type LoadResult,
  type OnboardingStatus,
  type Probe,
  type StageState,
} from "@/lib/onboarding";

const EMPTY_STATE = Object.fromEntries(
  STAGES.map((stage) => [stage, "pending" as StageState]),
) as Record<string, StageState>;

interface ConnectResponse {
  probe: Probe;
  source: string;
  status: OnboardingStatus;
}

/**
 * The DB Agent screen: point it at a database, watch it work out what the schema means,
 * then load the ledger into our model.
 *
 * The order the stages run in is the backend's, not this component's — every button
 * calls one endpoint and renders what comes back. That matters because the gates that
 * stop a mapping being frozen before it reconciles are server-side, and a screen that
 * re-implemented them would eventually disagree with them.
 *
 * The credential is typed here and sent once. Nothing on this screen ever renders it
 * back: the server returns a redacted URL and that is what is shown.
 */
export default function OnboardingScreen() {
  const router = useRouter();
  const [catalog, setCatalog] = useState<CompanyCatalog | null>(null);
  const [selected, setSelected] = useState<DemoCompany | null>(null);

  const [url, setUrl] = useState("");
  const [company, setCompany] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [controls, setControls] = useState({ ar: "", ap: "", cash: "" });

  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const [probe, setProbe] = useState<Probe | null>(null);
  const [introspection, setIntrospection] = useState<IntrospectResult | null>(null);
  const [classification, setClassification] = useState<ClassifyResult | null>(null);
  const [loadResult, setLoadResult] = useState<LoadResult | null>(null);
  const [ledger, setLedger] = useState<Ledger | null>(null);

  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchJson<CompanyCatalog>("/api/onboarding/companies")
      .then(setCatalog)
      .catch((cause: Error) => setError(cause.message));
    fetchJson<OnboardingStatus>("/api/onboarding")
      .then(setStatus)
      .catch(() => undefined);
  }, []);

  const run = useCallback(
    async <T,>(label: string, path: string, body?: unknown): Promise<T | null> => {
      setBusy(label);
      setError(null);
      try {
        return await fetchJson<T>(path, {
          method: "POST",
          body: JSON.stringify(body ?? {}),
        });
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
        return null;
      } finally {
        setBusy(null);
      }
    },
    [],
  );

  function pick(chosen: DemoCompany) {
    setSelected(chosen);
    setUrl(chosen.url);
    setCompany(chosen.company);
    setTenantId(chosen.key);
    setCurrency(chosen.currency);
    setControls({
      ar: String(chosen.control_balances.ar_control_minor),
      ap: String(chosen.control_balances.ap_control_minor),
      cash: String(chosen.control_balances.cash_control_minor),
    });
  }

  async function connect() {
    const result = await run<ConnectResponse>("connect", "/api/onboarding/connect", {
      url,
      company,
      tenant_id: tenantId || "tenant",
      currency,
    });
    if (!result) return;
    setProbe(result.probe);
    setStatus(result.status);
    setIntrospection(null);
    setClassification(null);
    setLoadResult(null);
    setLedger(null);
  }

  async function introspect() {
    const result = await run<IntrospectResult>("introspect", "/api/onboarding/introspect");
    if (!result) return;
    setIntrospection(result);
    setStatus(result.status);
  }

  async function classify() {
    const result = await run<ClassifyResult>("classify", "/api/onboarding/classify");
    if (!result) return;
    setClassification(result);
    setStatus(result.status);
  }

  async function load() {
    const result = await run<LoadResult>("load", "/api/onboarding/load", {
      ar_minor: Number(controls.ar || 0),
      ap_minor: Number(controls.ap || 0),
      cash_minor: Number(controls.cash || 0),
      currency,
    });
    if (!result) return;
    setLoadResult(result);
    setStatus(result.status);
    if (result.accepted) {
      fetchJson<Ledger>("/api/onboarding/ledger")
        .then(setLedger)
        .catch(() => undefined);
    }
  }

  async function loadDemoLedger() {
    await run("demo", "/api/data-source/demo");
    router.push("/war-room");
  }

  async function clearSource() {
    await run("clear", "/api/onboarding/reset");
    setProbe(null);
    setIntrospection(null);
    setClassification(null);
    setLoadResult(null);
    setLedger(null);
    setStatus(await fetchJson<OnboardingStatus>("/api/onboarding"));
  }

  const state = status?.state ?? EMPTY_STATE;
  const connected = Boolean(status?.connected);

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-4 px-4 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold text-ink">Data source — DB Agent</h1>
        <p className="font-mono text-xs text-ink-3">
          {status?.summary?.source ?? "not connected"}
          {status?.fingerprint_hash ? ` · fingerprint ${status.fingerprint_hash.slice(0, 12)}` : ""}
        </p>
      </div>

      <StageRail state={state} />

      {error ? (
        <p className="rounded-lg border border-critical bg-surface-1 px-4 py-3 text-xs leading-relaxed text-critical">
          ✗ {error}
        </p>
      ) : null}

      <Panel
        title="Available companies"
        subtitle={
          catalog?.available
            ? `${catalog.companies.length} demo tenants · each on a different source schema`
            : "demo databases not built on this host"
        }
      >
        <div className="px-4 py-3">
          {catalog?.available ? (
            <CompanyPicker
              companies={catalog.companies}
              selected={selected?.key ?? null}
              onSelect={pick}
              disabled={busy !== null}
            />
          ) : (
            <p className="text-xs leading-relaxed text-ink-2">
              {catalog?.hint ?? "Loading…"} You can still connect to any reachable database by
              filling the form below.
            </p>
          )}
        </div>
      </Panel>

      <Panel title="Connect" subtitle="the credential is sent once and never rendered back">
        <div className="grid gap-3 px-4 py-3 lg:grid-cols-2">
          <label className="flex flex-col gap-1 lg:col-span-2">
            <span className="text-[10px] tracking-wide text-ink-3 uppercase">
              Database URL — include the password; write permission is probed, not used
            </span>
            <input
              type="password"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="postgresql+psycopg://user:password@host:5432/database"
              autoComplete="off"
              spellCheck={false}
              className="rounded border border-line bg-surface-0 px-3 py-2 font-mono text-xs text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none"
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[10px] tracking-wide text-ink-3 uppercase">Company</span>
            <input
              value={company}
              onChange={(event) => setCompany(event.target.value)}
              placeholder="Helios Robotics"
              className="rounded border border-line bg-surface-0 px-3 py-2 text-xs text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none"
            />
          </label>

          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-[10px] tracking-wide text-ink-3 uppercase">Tenant id</span>
              <input
                value={tenantId}
                onChange={(event) => setTenantId(event.target.value)}
                placeholder="helios"
                className="rounded border border-line bg-surface-0 px-3 py-2 font-mono text-xs text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] tracking-wide text-ink-3 uppercase">Currency</span>
              <input
                value={currency}
                onChange={(event) => setCurrency(event.target.value.toUpperCase().slice(0, 3))}
                className="rounded border border-line bg-surface-0 px-3 py-2 font-mono text-xs text-ink focus:border-accent focus:outline-none"
              />
            </label>
          </div>

          <div className="flex flex-wrap items-center gap-2 lg:col-span-2">
            <Action
              label="Connect"
              busy={busy}
              name="connect"
              onClick={connect}
              enabled={url.length > 0 && company.length > 0}
            />
            <Action
              label="Read schema"
              busy={busy}
              name="introspect"
              onClick={introspect}
              enabled={connected}
            />
            <Action
              label="Classify"
              busy={busy}
              name="classify"
              onClick={classify}
              enabled={Boolean(introspection)}
            />
          </div>

          {probe ? (
            <dl className="grid gap-x-6 gap-y-1 text-[11px] lg:col-span-2 lg:grid-cols-4">
              <Fact label="Dialect" value={`${probe.dialect} ${probe.server_version}`} />
              <Fact label="Schema" value={probe.schema} />
              <Fact label="Tables" value={String(probe.table_count)} />
              <Fact
                label="Write permission"
                value={probe.can_write ? "granted" : "not granted"}
                tone={probe.can_write ? "text-good" : "text-warning"}
              />
              <div className="lg:col-span-4">
                <p className="text-[11px] leading-relaxed text-ink-3">{probe.write_note}</p>
              </div>
            </dl>
          ) : null}
        </div>
      </Panel>

      {introspection && classification ? (
        <SchemaMap introspection={introspection} classification={classification} />
      ) : null}

      {classification ? (
        <Panel
          title="Reconcile and load"
          subtitle="the mapping is held to your own control totals before a single row is committed"
        >
          <div className="flex flex-col gap-3 px-4 py-3">
            <p className="text-xs leading-relaxed text-ink-2">
              Enter the AR, AP and cash control balances from your trial balance, in minor units.
              The loader writes into a transaction, sums what it wrote, and compares. A mismatch
              rolls the whole load back — a mapping that does not tie out never becomes data.
            </p>

            <div className="grid gap-3 sm:grid-cols-3">
              {(
                [
                  ["ar", "AR control"],
                  ["ap", "AP control"],
                  ["cash", "Cash control"],
                ] as const
              ).map(([key, label]) => (
                <label key={key} className="flex flex-col gap-1">
                  <span className="text-[10px] tracking-wide text-ink-3 uppercase">
                    {label} (minor units)
                  </span>
                  <input
                    inputMode="numeric"
                    value={controls[key]}
                    onChange={(event) =>
                      setControls((previous) => ({
                        ...previous,
                        [key]: event.target.value.replace(/[^0-9-]/g, ""),
                      }))
                    }
                    className="rounded border border-line bg-surface-0 px-3 py-2 font-mono text-xs text-ink focus:border-accent focus:outline-none"
                  />
                  <span className="font-mono text-[10px] text-ink-3">
                    {formatMinor(Number(controls[key] || 0), currency)}
                  </span>
                </label>
              ))}
            </div>

            <div>
              <Action label="Load into WAR ROOM" busy={busy} name="load" onClick={load} enabled />
            </div>

            {loadResult ? <LoadOutcome result={loadResult} currency={currency} /> : null}
          </div>
        </Panel>
      ) : null}

      {ledger ? <LedgerPanel ledger={ledger} /> : null}

      {loadResult?.accepted ? (
        <Panel title="This is now the data source" subtitle="every screen was cleared and refilled">
          <div className="flex flex-wrap items-center gap-4 px-4 py-3">
            <p className="max-w-2xl flex-1 text-xs leading-relaxed text-ink-2">
              The Forecast, Agents, War Room, Recommendation, Approvals and Evidence screens are all
              views of this ledger now. Anything the previous source produced — its cycle, its agent
              runs, its investigation and its approval cards — was discarded when this one
              committed, because a figure that outlives the ledger it came from is how a bad number
              reaches a board pack.
            </p>
            <Link
              href="/war-room"
              className="rounded border border-accent px-4 py-2 text-xs text-accent hover:bg-surface-2"
            >
              Run the agents →
            </Link>
          </div>
        </Panel>
      ) : null}

      <Panel title="Or work against the recorded demo" subtitle="clearly labelled, deterministic">
        <div className="flex flex-wrap items-center gap-4 px-4 py-3">
          <p className="max-w-2xl flex-1 text-xs leading-relaxed text-ink-2">
            The NovaTech golden path is a recorded synthetic ledger with a Dodo feed, debt
            facilities and covenants — the sources a freshly onboarded database does not have.
            Loading it binds it the same way a tenant is bound, and clears the current one.
          </p>
          <button
            type="button"
            onClick={() => loadDemoLedger()}
            className="rounded border border-line px-4 py-2 text-xs text-ink-2 hover:bg-surface-2"
          >
            Use the demo ledger
          </button>
          <button
            type="button"
            onClick={() => clearSource()}
            className="rounded border border-line px-4 py-2 text-xs text-ink-3 hover:bg-surface-2"
          >
            Unload everything
          </button>
        </div>
      </Panel>
    </div>
  );
}

function Action({
  label,
  name,
  busy,
  onClick,
  enabled,
}: {
  label: string;
  name: string;
  busy: string | null;
  onClick: () => void;
  enabled: boolean;
}) {
  const running = busy === name;
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!enabled || busy !== null}
      className="rounded border border-accent px-3 py-1.5 text-xs text-accent transition-colors hover:bg-surface-2 disabled:border-line disabled:text-ink-3 disabled:hover:bg-transparent"
    >
      {running ? `${label}…` : label}
    </button>
  );
}

function Fact({
  label,
  value,
  tone = "text-ink-2",
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div className="flex gap-2">
      <dt className="text-ink-3">{label}</dt>
      <dd className={`font-mono ${tone}`}>{value}</dd>
    </div>
  );
}

function LoadOutcome({ result, currency }: { result: LoadResult; currency: string }) {
  const reconciliation = result.reconciliation;
  return (
    <div
      className={`rounded border px-3 py-3 ${
        result.accepted ? "border-good bg-surface-2" : "border-critical bg-surface-2"
      }`}
    >
      <p className={`text-xs font-medium ${result.accepted ? "text-good" : "text-critical"}`}>
        {result.accepted
          ? `✓ Reconciled and committed — ${result.mapped_rows.toLocaleString("en-US")} rows`
          : "✗ Reconciliation failed — the load was rolled back"}
      </p>

      {reconciliation && reconciliation.failures.length > 0 ? (
        <ul className="mt-2 flex flex-col gap-1">
          {reconciliation.failures.map((failure) => (
            <li key={failure} className="font-mono text-[11px] text-critical">
              {failure}
            </li>
          ))}
        </ul>
      ) : null}

      <dl className="mt-2 grid gap-x-6 gap-y-1 text-[11px] sm:grid-cols-3">
        <Fact label="Open AR" value={formatMinor(result.totals.open_invoices_minor, currency)} />
        <Fact
          label="Open AP"
          value={formatMinor(result.totals.open_vendor_invoices_minor, currency)}
        />
        <Fact label="Cash" value={formatMinor(result.totals.bank_transactions_minor, currency)} />
      </dl>

      <p className="mt-2 font-mono text-[11px] text-ink-3">
        {Object.entries(result.counts)
          .map(([entity, count]) => `${entity} ${count}`)
          .join("  ·  ")}
      </p>

      {result.mapping_path ? (
        <p className="mt-1 font-mono text-[11px] text-ink-3">frozen: {result.mapping_path}</p>
      ) : null}

      {result.assumptions.length > 0 ? (
        <ul className="mt-2 flex flex-col gap-1">
          {result.assumptions.map((assumption) => (
            <li key={assumption} className="text-[11px] leading-snug text-warning">
              ⚠ {assumption}
            </li>
          ))}
        </ul>
      ) : null}

      {result.reject_count > 0 ? (
        <details className="mt-2">
          <summary className="cursor-pointer text-[11px] text-serious">
            ▲ {result.reject_count} rows rejected — reported, not dropped
          </summary>
          <ul className="mt-1 flex flex-col gap-0.5">
            {result.rejects.map((reject) => (
              <li
                key={`${reject.entity}-${reject.source_key}`}
                className="font-mono text-[10px] text-ink-3"
              >
                {reject.entity} {reject.source_key}: {reject.reason}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function LedgerPanel({ ledger }: { ledger: Ledger }) {
  return (
    <Panel title="In our model" subtitle={`${ledger.company} · read back from the target database`}>
      <div className="flex flex-col gap-3 px-4 py-3">
        <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-3">
          <Fact label="Open AR" value={formatMinor(ledger.totals.open_ar_minor, ledger.currency)} />
          <Fact label="Open AP" value={formatMinor(ledger.totals.open_ap_minor, ledger.currency)} />
          <Fact label="Cash" value={formatMinor(ledger.totals.cash_minor, ledger.currency)} />
        </dl>

        <p className="font-mono text-[11px] text-ink-3">
          {Object.entries(ledger.counts)
            .map(([entity, count]) => `${entity} ${count}`)
            .join("  ·  ")}
        </p>

        {ledger.largest_open_receivables.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] border-collapse text-left">
              <caption className="pb-1 text-left text-[10px] tracking-wide text-ink-3 uppercase">
                Largest open receivables
              </caption>
              <thead>
                <tr className="text-[10px] tracking-wide text-ink-3 uppercase">
                  <th className="py-1 pr-4 font-normal">Invoice</th>
                  <th className="py-1 pr-4 font-normal">Customer</th>
                  <th className="py-1 pr-4 font-normal">Open</th>
                  <th className="py-1 pr-4 font-normal">Due</th>
                  <th className="py-1 font-normal">Age</th>
                </tr>
              </thead>
              <tbody>
                {ledger.largest_open_receivables.map((row) => (
                  <tr key={row.invoice_ref} className="border-t border-line/60">
                    <td className="py-1 pr-4 font-mono text-xs text-ink-2">{row.invoice_ref}</td>
                    <td className="py-1 pr-4 text-xs text-ink">{row.customer}</td>
                    <td className="py-1 pr-4 font-mono text-xs text-ink">
                      {formatMinor(row.open_minor, row.currency)}
                    </td>
                    <td className="py-1 pr-4 font-mono text-[11px] text-ink-3">{row.due_date}</td>
                    <td
                      className={`py-1 font-mono text-[11px] ${
                        row.days_past_due > 0 ? "text-serious" : "text-ink-3"
                      }`}
                    >
                      {row.days_past_due > 0 ? `${row.days_past_due}d late` : "current"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </Panel>
  );
}
