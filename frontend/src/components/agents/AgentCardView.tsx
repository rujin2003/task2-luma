"use client";

import { STATUS_MARK, STATUS_TONE, money, type AgentCard } from "@/lib/agents";

/**
 * One agent, as something you can read and then start.
 *
 * The card shows the agent's job, the tools it is allowed to call, and the model it is
 * routed to *before* it shows a button — because "start agent" is only a meaningful
 * offer if you know what you are starting. After a run it shows what the agent
 * concluded, its evidence, and the risks it raised, with a refusal rendered as an
 * outcome rather than as an error.
 */
export function AgentCardView({
  card,
  running,
  disabled,
  onStart,
}: {
  card: AgentCard;
  running: boolean;
  disabled: boolean;
  onStart: (role: string) => void;
}) {
  const run = card.last_run;
  const finding = run?.finding ?? null;

  return (
    <article className="flex flex-col rounded-lg border border-line bg-surface-1">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-line px-4 py-3">
        <div className="flex items-baseline gap-2">
          <h2 className="text-sm font-semibold text-ink">{card.title}</h2>
          <span className="font-mono text-[10px] text-ink-3">{card.role}</span>
        </div>
        {run ? (
          <span className={`inline-flex items-center gap-1.5 text-xs ${STATUS_TONE[run.status]}`}>
            <span aria-hidden>{running ? "↻" : STATUS_MARK[run.status]}</span>
            <span>{running ? "working" : run.status}</span>
            {!running ? (
              <span className="font-mono text-[10px] text-ink-3">{run.latency_ms}ms</span>
            ) : null}
          </span>
        ) : (
          <span className="text-xs text-ink-3">{running ? "↻ working" : "○ not started"}</span>
        )}
      </header>

      <div className="flex flex-1 flex-col gap-3 px-4 py-3">
        <p className="text-xs leading-relaxed text-ink-2">{card.purpose}</p>
        <p className="text-[11px] leading-relaxed text-ink-3 italic">“{card.asks}”</p>

        <dl className="flex flex-col gap-1 text-[11px]">
          <div className="flex gap-2">
            <dt className="shrink-0 text-ink-3">Model</dt>
            <dd className="font-mono text-ink-2">{card.model}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="shrink-0 text-ink-3">Tools</dt>
            <dd className="font-mono break-all text-ink-3">{card.tools.join(", ")}</dd>
          </div>
        </dl>

        {finding ? (
          <div className="flex flex-col gap-2 rounded border border-line bg-surface-2 px-3 py-2.5">
            <p className="text-xs leading-snug font-medium text-ink">{finding.headline}</p>
            {finding.quantum ? (
              <p className="font-mono text-xs text-good">{money(finding.quantum)}</p>
            ) : null}
            {finding.detail ? (
              <p className="text-[11px] leading-relaxed text-ink-2">{finding.detail}</p>
            ) : null}

            {finding.risks.length > 0 ? (
              <ul className="flex flex-col gap-0.5">
                {finding.risks.map((risk) => (
                  <li key={risk} className="text-[11px] leading-snug text-warning">
                    ⚠ {risk}
                  </li>
                ))}
              </ul>
            ) : null}

            {finding.recommended_actions.length > 0 ? (
              <ul className="flex flex-col gap-0.5">
                {finding.recommended_actions.map((action, index) => (
                  <li
                    key={`${action.kind}-${index}`}
                    className="text-[11px] leading-snug text-ink-2"
                  >
                    ▲ {action.kind}
                    {action.counterparty ? ` · ${action.counterparty}` : ""}
                    {action.amount ? ` · ${money(action.amount)}` : ""}
                    {action.delay_days ? ` · +${action.delay_days}d` : ""}
                  </li>
                ))}
              </ul>
            ) : null}

            {finding.evidence.length > 0 ? (
              <details>
                <summary className="cursor-pointer text-[10px] tracking-wide text-ink-3 uppercase">
                  Evidence ({finding.evidence.length})
                </summary>
                <ul className="mt-1 flex flex-col gap-1">
                  {finding.evidence.map((item) => (
                    <li key={item.reference} className="text-[10px] leading-snug text-ink-3">
                      <span className="font-mono text-ink-2">{item.reference}</span> —{" "}
                      {item.excerpt}
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </div>
        ) : null}

        {run && !finding && run.failure_reason ? (
          <p className={`text-[11px] leading-relaxed ${STATUS_TONE[run.status]}`}>
            {STATUS_MARK[run.status]} {run.failure_reason}
          </p>
        ) : null}

        {run && run.tool_calls.length > 0 ? (
          <p className="font-mono text-[10px] text-ink-3">
            {run.tool_calls
              .map((call) => `${call.tool}${call.error ? " ✗" : ` ${call.duration_ms}ms`}`)
              .join("  ·  ")}
          </p>
        ) : null}
      </div>

      <footer className="border-t border-line px-4 py-2.5">
        <button
          type="button"
          onClick={() => onStart(card.role)}
          disabled={disabled || running}
          className="rounded border border-accent px-3 py-1.5 text-xs text-accent transition-colors hover:bg-surface-2 disabled:border-line disabled:text-ink-3 disabled:hover:bg-transparent"
        >
          {running ? "Working…" : run ? "Run again" : "Start agent"}
        </button>
      </footer>
    </article>
  );
}
