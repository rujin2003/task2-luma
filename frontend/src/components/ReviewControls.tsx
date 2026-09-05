import { Panel } from "./primitives";

/**
 * The human judgement step. Wired up in Phase 7 -- an override becomes a first-class
 * recorded object with a stated reason, and publishing locks the ForecastVersion so it
 * becomes next week's baseline.
 */
export function ReviewControls({
  versionId,
  published,
}: {
  versionId: string;
  published: boolean;
}) {
  return (
    <Panel title="Review and publish" subtitle={published ? "Published" : "Draft"}>
      <div className="flex flex-col gap-3 px-4 py-4">
        <p className="text-xs leading-relaxed text-ink-2">
          Version <span className="font-mono text-ink">{versionId}</span> is a draft. Publishing
          locks it as next week&apos;s baseline and feeds the accuracy roll-forward.
        </p>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled
            className="rounded border border-line px-3 py-1.5 text-xs text-ink-2 disabled:opacity-60"
          >
            Override an assumption
          </button>
          <button
            type="button"
            disabled
            className="rounded border border-line px-3 py-1.5 text-xs text-ink-2 disabled:opacity-60"
          >
            Send to Treasurer
          </button>
          <button
            type="button"
            disabled
            className="rounded border border-good px-3 py-1.5 text-xs text-good disabled:opacity-60"
          >
            Publish version
          </button>
        </div>
        <p className="text-xs text-ink-3">Controls activate with the weekly cycle in Phase 7.</p>
      </div>
    </Panel>
  );
}
