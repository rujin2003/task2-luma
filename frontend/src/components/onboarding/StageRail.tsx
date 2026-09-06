import { STAGES, STAGE_LABEL, STAGE_NOTE, type StageState } from "@/lib/onboarding";

const MARK: Record<StageState, string> = { pending: "○", ok: "✓", failed: "✗" };
const TONE: Record<StageState, string> = {
  pending: "text-ink-3",
  ok: "text-good",
  failed: "text-critical",
};

/**
 * The eight stages, always all eight, with the ones that have not run shown as pending
 * rather than hidden. A pipeline that reveals its steps as it goes leaves the operator
 * unable to tell how much is left, which is exactly the anxiety a credential prompt
 * should not be adding to.
 */
export function StageRail({ state }: { state: Record<string, StageState> }) {
  return (
    <ol className="grid gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-2 lg:grid-cols-4">
      {STAGES.map((stage, index) => {
        const status = state[stage] ?? "pending";
        return (
          <li key={stage} className="bg-surface-1 px-3 py-2.5">
            <div className="flex items-baseline gap-2">
              <span className={`font-mono text-xs ${TONE[status]}`} aria-hidden>
                {MARK[status]}
              </span>
              <span className="font-mono text-[10px] text-ink-3">{index + 1}</span>
              <span className="text-xs font-medium text-ink">{STAGE_LABEL[stage]}</span>
              <span className="sr-only">{status}</span>
            </div>
            <p className="mt-1 pl-6 text-[11px] leading-snug text-ink-3">{STAGE_NOTE[stage]}</p>
          </li>
        );
      })}
    </ol>
  );
}
