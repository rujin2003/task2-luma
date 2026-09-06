import { formatMoney } from "@/lib/contracts";
import type { InvestigationPhase } from "@/lib/events";
import type { Opening, PlanSummary } from "@/lib/warroom";
import { AGENT_LABEL, PHASE_LABEL, PHASE_ORDER, formatElapsed } from "@/lib/warroom";

import { Traced } from "../evidence/Traced";
import { Panel } from "../primitives";

/**
 * What opened this war room, and where the machine has got to.
 *
 * The trigger is rendered first and in full because it is the claim everything downstream
 * rests on: a specific constraint, a dated week, a quantified gap and the rows that show
 * it. A war room that opened on "cash looks tight" is a war room that will recommend
 * something expensive for a reason nobody can check afterwards.
 */
export function InvestigationHeader({
  opening,
  phase,
  phasesSeen,
  elapsedMs,
  plan,
}: {
  opening?: Opening;
  phase?: InvestigationPhase;
  phasesSeen: InvestigationPhase[];
  elapsedMs?: number | null;
  plan?: PlanSummary;
}) {
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Panel
        title="Trigger"
        subtitle={opening ? `detected ${opening.detectedAt.slice(0, 10)}` : undefined}
        className="lg:col-span-2"
      >
        <div className="px-4 py-3">
          {opening ? (
            <>
              <p className="text-sm leading-relaxed text-ink">{opening.trigger}</p>
              {opening.breachDescription ? (
                <p className="mt-2 text-xs leading-relaxed text-ink-2">
                  {opening.breachDescription}
                </p>
              ) : null}

              <dl className="mt-3 flex flex-wrap gap-x-8 gap-y-2 font-mono text-xs">
                {opening.quantum ? (
                  <div>
                    <dt className="text-ink-3">Shortfall</dt>
                    <dd className="text-critical tabular-nums">
                      {formatMoney(opening.quantum, { compact: true })}
                    </dd>
                  </div>
                ) : null}
                {opening.breachThreshold ? (
                  <div>
                    <dt className="text-ink-3">Threshold</dt>
                    <dd className="text-ink-2 tabular-nums">{opening.breachThreshold}</dd>
                  </div>
                ) : null}
                {opening.breachWeek != null ? (
                  <div>
                    <dt className="text-ink-3">Week</dt>
                    <dd className="text-ink-2 tabular-nums">W{opening.breachWeek}</dd>
                  </div>
                ) : null}
                <div>
                  <dt className="text-ink-3">Elapsed</dt>
                  <dd className="text-ink-2 tabular-nums">{formatElapsed(elapsedMs)}</dd>
                </div>
              </dl>

              <p className="mt-3 flex flex-wrap gap-x-3 font-mono text-xs text-ink-3">
                {opening.evidence.map((evidence) => (
                  <Traced
                    key={evidence.reference}
                    reference={evidence.reference}
                    label="Breach evidence"
                  >
                    {evidence.reference}
                  </Traced>
                ))}
              </p>
            </>
          ) : (
            <p className="text-sm text-ink-3">
              No war room is open. This screen exists only during an escalation.
            </p>
          )}
        </div>
      </Panel>

      <Panel title="Plan" subtitle={plan?.planId}>
        <div className="px-4 py-3">
          <p className="text-xs text-ink-3">Invoked</p>
          <p className="mt-1 text-sm text-ink">
            {plan ? plan.invoked.map((agent) => AGENT_LABEL[agent]).join(", ") : "--"}
          </p>

          {/* The justification for *not* calling an agent is part of the plan, not an
              omission from it: a pure-AR incident must be able to say why Dodo was left
              out, and "it did not come up" is not an answer. */}
          <p className="mt-3 text-xs text-ink-3">Not invoked</p>
          {plan && plan.skipped.length > 0 ? (
            <ul className="mt-1 space-y-1.5">
              {plan.skipped.map((skip) => (
                <li key={skip.agent} className="text-xs leading-relaxed text-ink-2">
                  <span className="text-ink">{AGENT_LABEL[skip.agent]}</span> — {skip.reason}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-xs text-ink-2">
              Every agent this tenant can run was in scope for this incident.
            </p>
          )}
        </div>
      </Panel>

      <div className="lg:col-span-3">
        <PhaseRail phase={phase} seen={phasesSeen} />
      </div>
    </div>
  );
}

/** The state machine, drawn. Bounded and ordered, so an analyst can see where it stalled. */
function PhaseRail({ phase, seen }: { phase?: InvestigationPhase; seen: InvestigationPhase[] }) {
  return (
    <ol
      className="flex flex-wrap items-center gap-x-2 gap-y-2 rounded-lg border border-line bg-surface-1 px-4 py-3"
      aria-label="Investigation phases"
    >
      {PHASE_ORDER.map((step, index) => {
        const visited = seen.includes(step);
        const current = phase === step;
        return (
          <li key={step} className="flex items-center gap-2">
            <span
              className={`text-xs ${
                current
                  ? "font-medium text-accent"
                  : visited
                    ? "text-ink-2"
                    : /* Not reached. Rendered, not hidden: an investigation that never
                         got to stress testing should look like one that never got there. */
                      "text-ink-3 line-through decoration-line"
              }`}
            >
              {visited ? "✓ " : ""}
              {PHASE_LABEL[step]}
            </span>
            {index < PHASE_ORDER.length - 1 ? (
              <span className="text-ink-3" aria-hidden>
                →
              </span>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
