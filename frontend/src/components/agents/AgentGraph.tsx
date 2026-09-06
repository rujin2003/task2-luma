"use client";

import { STATUS_MARK, type AgentCard, type AgentEdge } from "@/lib/agents";

/**
 * The agent space as a graph: who feeds whom, and what has actually run.
 *
 * The layering is computed from the edges rather than hard-coded, so an agent added to
 * the roster on the server appears here in the right column without a second edit. Nodes
 * are laid out on a fixed viewBox and the SVG scales — a graph that reflows differently
 * on every window width is a graph nobody can describe to a colleague over a call.
 *
 * Colour is never the only channel: every node carries its status mark, and an unsatisfied
 * edge is dashed as well as dimmed.
 */

const NODE_W = 190;
const NODE_H = 58;
const COL_GAP = 110;
const ROW_GAP = 22;
const PAD = 12;

const TONE: Record<string, { stroke: string; text: string }> = {
  complete: { stroke: "var(--color-good, #0ca30c)", text: "var(--color-good, #0ca30c)" },
  degraded: { stroke: "var(--color-warning, #fab219)", text: "var(--color-warning, #fab219)" },
  refused: { stroke: "var(--color-serious, #ec835a)", text: "var(--color-serious, #ec835a)" },
  timeout: { stroke: "var(--color-serious, #ec835a)", text: "var(--color-serious, #ec835a)" },
  failed: { stroke: "var(--color-critical, #d03b3b)", text: "var(--color-critical, #d03b3b)" },
  running: { stroke: "var(--color-accent, #3987e5)", text: "var(--color-accent, #3987e5)" },
};

/** Longest-path layering: a node sits one column right of its furthest upstream node. */
function layers(cards: AgentCard[], edges: AgentEdge[]): Map<string, number> {
  const depth = new Map<string, number>(cards.map((card) => [card.role, 0]));
  // Six nodes and a handful of edges: relaxing |V| times is exact and needs no cycle check.
  for (let pass = 0; pass < cards.length; pass += 1) {
    for (const edge of edges) {
      const from = depth.get(edge.from);
      const to = depth.get(edge.to);
      if (from === undefined || to === undefined) continue;
      if (to < from + 1) depth.set(edge.to, from + 1);
    }
  }
  return depth;
}

export function AgentGraph({
  cards,
  edges,
  selected,
  onSelect,
}: {
  cards: AgentCard[];
  edges: AgentEdge[];
  selected?: string | null;
  onSelect?: (role: string) => void;
}) {
  const depth = layers(cards, edges);
  const columns: AgentCard[][] = [];
  for (const card of cards) {
    const column = depth.get(card.role) ?? 0;
    (columns[column] ??= []).push(card);
  }

  const positions = new Map<string, { x: number; y: number }>();
  columns.forEach((column, index) => {
    column.forEach((card, row) => {
      positions.set(card.role, {
        x: PAD + index * (NODE_W + COL_GAP),
        y: PAD + row * (NODE_H + ROW_GAP),
      });
    });
  });

  const width = PAD * 2 + columns.length * NODE_W + Math.max(columns.length - 1, 0) * COL_GAP;
  const height =
    PAD * 2 + Math.max(...columns.map((column) => column.length), 1) * (NODE_H + ROW_GAP) - ROW_GAP;

  const satisfied = new Set(
    cards.flatMap((card) =>
      card.depends_on.filter((item) => item.satisfied).map((item) => `${item.role}->${card.role}`),
    ),
  );

  return (
    <div className="overflow-x-auto px-4 py-4">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-auto w-full min-w-[560px]"
        role="img"
        aria-label="Agent dependency graph"
      >
        <defs>
          <marker
            id="agent-arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M0,0 L10,5 L0,10 z" fill="currentColor" />
          </marker>
        </defs>

        {edges.map((edge) => {
          const from = positions.get(edge.from);
          const to = positions.get(edge.to);
          if (!from || !to) return null;
          const x1 = from.x + NODE_W;
          const y1 = from.y + NODE_H / 2;
          const x2 = to.x;
          const y2 = to.y + NODE_H / 2;
          const mid = (x1 + x2) / 2;
          const live = satisfied.has(`${edge.from}->${edge.to}`);
          return (
            <path
              key={`${edge.from}-${edge.to}`}
              d={`M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`}
              fill="none"
              className={live ? "text-good" : "text-ink-3"}
              stroke="currentColor"
              strokeWidth={live ? 1.6 : 1.1}
              strokeDasharray={live ? undefined : "4 3"}
              markerEnd="url(#agent-arrow)"
              opacity={live ? 0.95 : 0.55}
            />
          );
        })}

        {cards.map((card) => {
          const at = positions.get(card.role);
          if (!at) return null;
          const status = card.last_run?.status;
          const tone = status ? TONE[status] : undefined;
          const active = selected === card.role;
          return (
            <g
              key={card.role}
              transform={`translate(${at.x} ${at.y})`}
              className={onSelect ? "cursor-pointer" : undefined}
              onClick={() => onSelect?.(card.role)}
            >
              <rect
                width={NODE_W}
                height={NODE_H}
                rx={7}
                className="fill-surface-2"
                stroke={tone?.stroke ?? "var(--color-line, #383835)"}
                strokeWidth={active ? 2 : 1}
              />
              <text x={12} y={22} className="fill-ink text-[12px] font-semibold">
                {card.title}
              </text>
              <text x={12} y={38} className="fill-ink-3 font-mono text-[9px]">
                {card.role}
              </text>
              <text x={12} y={51} className="fill-ink-3 text-[9px]">
                {card.depends_on.length > 0
                  ? `needs ${card.depends_on.map((item) => item.title).join(", ")}`
                  : "independent"}
              </text>
              <text
                x={NODE_W - 12}
                y={22}
                textAnchor="end"
                className="text-[12px]"
                fill={tone?.text ?? "var(--color-ink-3, #8b8a82)"}
              >
                {status ? STATUS_MARK[status] : "○"}
              </text>
              {card.missing_sources.length > 0 ? (
                <text x={NODE_W - 12} y={48} textAnchor="end" className="fill-warning text-[9px]">
                  no {card.missing_sources.join("/")}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>

      <p className="mt-3 text-[11px] leading-relaxed text-ink-3">
        A solid arrow is a dependency that is satisfied — the upstream agent has produced a finding
        in this session. A dashed arrow is one that has not run yet: the downstream agent will still
        start, and will report what it was missing rather than pretending it had the input.
      </p>
    </div>
  );
}
