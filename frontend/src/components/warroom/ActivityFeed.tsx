import { MARK_GLYPH } from "@/lib/events";
import type { StatusMark } from "@/lib/events";
import type { FeedLine } from "@/lib/warroom";
import { AGENT_LABEL } from "@/lib/warroom";

import { Panel } from "../primitives";

const MARK_CLASS: Record<StatusMark, string> = {
  ok: "text-good",
  warn: "text-warning",
  working: "text-accent",
  fail: "text-critical",
  info: "text-ink-3",
};

/**
 * Concise status lines, and nothing else.
 *
 * The temptation on a screen like this is to show the model's working -- it looks
 * impressive and it fills space. It is also the fastest way to make a treasurer stop
 * trusting the output, because reasoning that reads well is not reasoning that is right,
 * and a screen that shows both gives no way to tell them apart. So the feed renders the
 * envelope only: a glyph, a line, a sequence number. Every claim worth acting on arrives
 * as a finding with evidence attached, and those have their own panel.
 */
export function ActivityFeed({ lines }: { lines: FeedLine[] }) {
  return (
    <Panel title="Activity" subtitle={`${lines.length} events`}>
      <ol className="max-h-[32rem] divide-y divide-line overflow-y-auto">
        {lines.map((line) => (
          <li key={line.seq} className="flex items-baseline gap-3 px-4 py-1.5">
            <span className="font-mono text-xs text-ink-3 tabular-nums">
              {String(line.seq).padStart(3, "0")}
            </span>
            <span className={`${MARK_CLASS[line.mark]}`} aria-hidden>
              {MARK_GLYPH[line.mark]}
            </span>
            <span className="flex-1 text-xs leading-relaxed text-ink-2">
              {line.agent ? <span className="text-ink-3">{AGENT_LABEL[line.agent]} · </span> : null}
              {line.text}
            </span>
          </li>
        ))}
      </ol>
    </Panel>
  );
}
