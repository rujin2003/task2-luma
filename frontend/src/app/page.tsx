"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { NoSource, SourceLine, money } from "@/components/SourceBanner";
import { Panel, StatusTag } from "@/components/primitives";
import { fetchJson } from "@/lib/api";
import { getPosition, isNoSource, type Position } from "@/lib/position";

/**
 * The Forecast screen: where this tenant actually stands, and what that rests on.
 *
 * Everything here is read through the tool layer — the same tools the Cash Forecast agent
 * calls — so a number on this screen and the number an agent argues about are the same
 * number from the same code path. The thirteen-week series is a direct-method projection
 * of the loaded ledger: open receivables weighted by the aging band's collection
 * probability, open payables at their due date, opening cash from settled bank movements.
 *
 * What the tenant cannot support is listed, by tool, with the reason. That block is the
 * most important thing on the page: a forecast that quietly omitted a missing input would
 * read exactly like one that had it.
 */
export default function ForecastScreen() {
  const [position, setPosition] = useState<Position | null>(null);
  const [empty, setEmpty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [floor, setFloor] = useState("");
  const [liquidity30, setLiquidity30] = useState("");

  const apply = useCallback((next: Position) => {
    setPosition(next);
    setEmpty(false);
    setFloor(((next.liquidity?.floor.minor_units ?? 0) / 100).toFixed(2));
    setLiquidity30(
      (
        (next.constraints?.constraints.find((row) => row.constraint_id === "min_30d_liquidity")
          ?.money_threshold?.minor_units ?? 0) / 100
      ).toFixed(2),
    );
  }, []);

  useEffect(() => {
    getPosition()
      .then(apply)
      .catch((cause: Error) => (isNoSource(cause) ? setEmpty(true) : setError(cause.message)));
  }, [apply]);

  async function savePolicy() {
    setError(null);
    try {
      const next = await fetchJson<Position>("/api/data-source/policy", {
        method: "POST",
        body: JSON.stringify({
          min_unrestricted_cash_minor: Math.round(Number(floor) * 100),
          min_30d_liquidity_minor: Math.round(Number(liquidity30) * 100),
        }),
      });
      apply(next);
      setEditing(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  if (empty) return <NoSource what="The forecast" />;
  if (!position) {
    return (
      <p className="px-4 py-8 text-xs text-ink-3">
        {error ? `✗ ${error}` : "Reading the position…"}
      </p>
    );
  }

  const { liquidity, forecast, aging, assumptions, capabilities, unavailable } = position;
  const breached = Boolean(
    liquidity && liquidity.min_cash.minor_units < liquidity.floor.minor_units,
  );
  const worst = forecast?.weeks.reduce(
    (low, week) => (week.closing_cash.minor_units < low.closing_cash.minor_units ? week : low),
    forecast.weeks[0],
  );
  const peak = Math.max(
    ...(forecast?.weeks.map((week) => Math.abs(week.closing_cash.minor_units)) ?? [1]),
    liquidity?.floor.minor_units ?? 1,
  );

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-4 px-4 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold text-ink">Forecast</h1>
        <SourceLine
          source={position.source}
          extra={forecast ? `${forecast.version_id} · unpublished draft` : undefined}
        />
      </div>

      {error ? (
        <p className="rounded-lg border border-critical bg-surface-1 px-4 py-3 text-xs text-critical">
          ✗ {error}
        </p>
      ) : null}

      {liquidity ? (
        <div className="grid grid-cols-2 gap-y-6 rounded-lg border border-line bg-surface-1 p-4 md:grid-cols-3 lg:grid-cols-5">
          {[
            {
              label: "Cash today",
              value: money(liquidity.cash_today, true),
              note: "settled bank movements",
            },
            {
              label: "13-week minimum",
              value: money(liquidity.min_cash, true),
              note: `week ${liquidity.min_cash_week}`,
            },
            { label: "Policy floor", value: money(liquidity.floor, true), note: "configurable" },
            {
              label: "Weeks above floor",
              value: `${liquidity.runway_weeks}`,
              note: "before the first breach",
            },
            {
              label: "Open receivables",
              value: money(aging?.total, true),
              note: `${aging?.buckets.reduce((n, b) => n + b.invoice_count, 0) ?? 0} invoices`,
            },
          ].map((tile) => (
            <div
              key={tile.label}
              className="flex flex-col gap-1 border-l border-line px-4 first:border-l-0 first:pl-0"
            >
              <span className="text-xs tracking-wide text-ink-3 uppercase">{tile.label}</span>
              <span className="font-mono text-xl tabular-nums text-ink">{tile.value}</span>
              <span className="text-xs text-ink-3">{tile.note}</span>
            </div>
          ))}
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-4">
        {breached ? (
          <StatusTag status="critical" label="Projected below the policy floor" />
        ) : (
          <StatusTag status="good" label="Projected above the policy floor for 13 weeks" />
        )}
        <button
          type="button"
          onClick={() => setEditing((was) => !was)}
          className="rounded border border-line px-3 py-1.5 text-xs text-ink-2 hover:bg-surface-2"
        >
          {editing ? "Cancel" : "Set this tenant's liquidity policy"}
        </button>
        <Link
          href="/war-room"
          className="rounded border border-accent px-3 py-1.5 text-xs text-accent hover:bg-surface-2"
        >
          Run the agents →
        </Link>
      </div>

      {editing ? (
        <Panel title="Treasury policy" subtitle={`in ${position.source.currency}`}>
          <div className="flex flex-wrap items-end gap-4 px-4 py-3">
            <label className="flex flex-col gap-1 text-xs text-ink-2">
              Minimum unrestricted cash
              <input
                value={floor}
                onChange={(event) => setFloor(event.target.value)}
                inputMode="decimal"
                className="w-48 rounded border border-line bg-surface-2 px-3 py-2 font-mono text-xs text-ink"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-ink-2">
              Minimum 30-day liquidity
              <input
                value={liquidity30}
                onChange={(event) => setLiquidity30(event.target.value)}
                inputMode="decimal"
                className="w-48 rounded border border-line bg-surface-2 px-3 py-2 font-mono text-xs text-ink"
              />
            </label>
            <button
              type="button"
              onClick={savePolicy}
              className="rounded border border-accent px-3 py-2 text-xs text-accent hover:bg-surface-2"
            >
              Save and re-evaluate
            </button>
            <p className="max-w-md text-[11px] leading-relaxed text-ink-3">
              The shipped thresholds belong to the NovaTech reference company. Holding a different
              balance sheet to them would put someone else&rsquo;s number on this screen. Saving
              clears the cycle that was run against the old policy.
            </p>
          </div>
        </Panel>
      ) : null}

      {forecast ? (
        <Panel
          title="Thirteen-week direct forecast"
          subtitle={`derived from the loaded ledger · ${forecast.weeks.length} weeks`}
        >
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-line text-left text-ink-3">
                <th className="px-4 py-2 font-normal">Week</th>
                <th className="px-4 py-2 font-normal">Ending</th>
                <th className="px-4 py-2 text-right font-normal">Closing cash</th>
                <th className="px-4 py-2 font-normal">Against floor</th>
              </tr>
            </thead>
            <tbody>
              {forecast.weeks.map((week) => {
                const width = Math.max(
                  2,
                  Math.round((Math.abs(week.closing_cash.minor_units) / peak) * 100),
                );
                return (
                  <tr
                    key={week.week_index}
                    className={`border-b border-line/60 ${
                      week.week_index === worst?.week_index ? "bg-surface-2" : ""
                    }`}
                  >
                    <td className="px-4 py-1.5 font-mono text-ink-3">W{week.week_index}</td>
                    <td className="px-4 py-1.5 font-mono text-ink-2">{week.week_ending}</td>
                    <td
                      className={`px-4 py-1.5 text-right font-mono tabular-nums ${
                        week.breaches_floor ? "text-critical" : "text-ink"
                      }`}
                    >
                      {money(week.closing_cash)}
                    </td>
                    <td className="px-4 py-1.5">
                      <span
                        className={`inline-block h-2 rounded-sm ${
                          week.breaches_floor ? "bg-critical" : "bg-good"
                        }`}
                        style={{ width: `${width}%` }}
                        aria-hidden
                      />
                      <span className="ml-2 text-[10px] text-ink-3">
                        {week.breaches_floor ? "below floor" : "above floor"}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Panel>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        {aging ? (
          <Panel title="Receivables aging" subtitle={money(aging.total)}>
            <ul className="divide-y divide-line">
              {aging.buckets.map((bucket) => (
                <li
                  key={bucket.label}
                  className="flex items-baseline justify-between gap-4 px-4 py-2 text-xs"
                >
                  <span className="text-ink-2">{bucket.label}</span>
                  <span className="text-ink-3">{bucket.invoice_count} invoices</span>
                  <span className="font-mono tabular-nums text-ink">{money(bucket.amount)}</span>
                </li>
              ))}
            </ul>
          </Panel>
        ) : null}

        {assumptions ? (
          <Panel title="What the forecast rests on" subtitle={`${assumptions.rows.length} drivers`}>
            <ul className="divide-y divide-line">
              {assumptions.rows.map((row) => (
                <li key={row.reference} className="flex flex-col gap-0.5 px-4 py-2">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-xs text-ink">{row.driver}</span>
                    <span className="font-mono text-xs text-ink-2">{row.value_display}</span>
                  </div>
                  <span className="text-[10px] text-ink-3">
                    {row.category} · refreshed {row.last_refreshed} ({row.days_since_refresh}d)
                    {row.stale ? " · ⚠ stale" : ""} · {row.reference}
                  </span>
                </li>
              ))}
            </ul>
          </Panel>
        ) : null}
      </div>

      {Object.keys(unavailable).length > 0 ? (
        <Panel title="What this source cannot answer" subtitle="stated, not omitted">
          <ul className="divide-y divide-line">
            {Object.entries(unavailable).map(([tool, reason]) => (
              <li key={tool} className="px-4 py-2 text-xs leading-relaxed text-warning">
                ⚠ <span className="font-mono">{tool}</span> — {reason}
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}

      {capabilities ? (
        <Panel
          title="Sources"
          subtitle={`${capabilities.available_sources.length} available · ${capabilities.missing_sources.length} missing`}
        >
          <ul className="grid gap-x-6 px-4 py-3 md:grid-cols-2">
            {[...capabilities.available_sources, ...capabilities.missing_sources].map((name) => {
              const has = capabilities.available_sources.includes(name);
              return (
                <li key={name} className="flex gap-2 py-1 text-[11px] leading-relaxed">
                  <span className={has ? "text-good" : "text-ink-3"} aria-hidden>
                    {has ? "✓" : "○"}
                  </span>
                  <span className="font-mono text-ink-2">{name}</span>
                  <span className="text-ink-3">{capabilities.notes[name]}</span>
                </li>
              );
            })}
          </ul>
        </Panel>
      ) : null}
    </div>
  );
}
