/**
 * The Evidence Explorer's model: click any number, walk down to the source ledger row.
 *
 * The rule this encodes is that a number on screen is either traceable or explicitly
 * marked as untraceable. There is no third state where the UI shows a figure and quietly
 * declines to say where it came from -- an unresolvable reference renders as a visible
 * gap, because a broken chain the analyst can see is recoverable and one they cannot is
 * how a bad number reaches a board pack.
 */

import { EVIDENCE_ROWS } from "@/fixtures/evidence";

export interface EvidenceRecord {
  reference: string;
  source: string;
  excerpt: string;
  as_of: string | null;
  resolved: boolean;
  fields: Record<string, string>;
  /** Rows this one was computed from. An aggregate is a node, not a leaf. */
  derived_from: string[];
}

const BY_REFERENCE = new Map(EVIDENCE_ROWS.map((row) => [row.reference, row]));

export function lookup(reference: string): EvidenceRecord | undefined {
  return BY_REFERENCE.get(reference);
}

export function allEvidence(): EvidenceRecord[] {
  return EVIDENCE_ROWS;
}

/** `source:record_id#field` split for display. A reference that will not parse is a bug. */
export function parseReference(reference: string): {
  source: string;
  recordId: string;
  field?: string;
} {
  const colon = reference.indexOf(":");
  const source = colon === -1 ? reference : reference.slice(0, colon);
  const rest = colon === -1 ? "" : reference.slice(colon + 1);
  const [recordId, field] = rest.split("#");
  return { source, recordId, field };
}

export interface ProvenanceNode {
  reference: string;
  record?: EvidenceRecord;
  depth: number;
}

/**
 * The walk from a cited number down to the rows underneath it.
 *
 * Breadth-first and cycle-safe: fixtures are hand-made today and engine-made later, and
 * neither is worth trusting with an unbounded recursion in a render path.
 */
export function provenanceChain(reference: string, maxDepth = 3): ProvenanceNode[] {
  const seen = new Set<string>();
  const nodes: ProvenanceNode[] = [];
  const queue: Array<{ reference: string; depth: number }> = [{ reference, depth: 0 }];

  while (queue.length > 0) {
    const { reference: current, depth } = queue.shift()!;
    if (seen.has(current)) continue;
    seen.add(current);

    const record = lookup(current);
    nodes.push({ reference: current, record, depth });

    if (!record || depth >= maxDepth) continue;
    for (const parent of record.derived_from) {
      queue.push({ reference: parent, depth: depth + 1 });
    }
  }

  return nodes;
}

/** Rows whose derivation includes this one -- "what rests on this number". */
export function dependents(reference: string): EvidenceRecord[] {
  return EVIDENCE_ROWS.filter((row) => row.derived_from.includes(reference));
}

export function searchEvidence(query: string): EvidenceRecord[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return EVIDENCE_ROWS;
  return EVIDENCE_ROWS.filter(
    (row) =>
      row.reference.toLowerCase().includes(needle) ||
      row.excerpt.toLowerCase().includes(needle) ||
      Object.values(row.fields).some((value) => value.toLowerCase().includes(needle)),
  );
}
