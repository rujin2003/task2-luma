/**
 * Mirrors the resolved evidence rows into the frontend.
 *
 *     npm run gen:evidence
 *
 * The Evidence Explorer must show what the ledger says, not a second copy of it written
 * by hand -- a provenance chain that disagrees with its own source is worse than no
 * provenance chain at all. So this file is generated, never edited, and
 * `tests/unit/test_evidence_fixture_parity.py` fails the build if it drifts.
 *
 * The source is the same fixture the backend's `resolve_evidence` tool reads. When Person
 * 1's engine lands, this is replaced by a fetch against their API and this script goes.
 */

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const SOURCE = resolve(here, "../../tests/fixtures/tools/resolve_evidence.json");
const TARGET = resolve(here, "../src/fixtures/evidence.ts");

const rows = JSON.parse(readFileSync(SOURCE, "utf8"));

const records = Object.values(rows)
  .map((row) => {
    const { derived_from: derivedFrom, ...fields } = row.fields ?? {};
    return {
      reference: row.reference,
      source: row.source,
      excerpt: row.excerpt,
      as_of: row.as_of ?? null,
      resolved: row.resolved !== false,
      fields,
      derived_from: derivedFrom
        ? derivedFrom
            .split(",")
            .map((part) => part.trim())
            .filter(Boolean)
        : [],
    };
  })
  .sort((a, b) => a.reference.localeCompare(b.reference));

const body = `/**
 * GENERATED FILE -- do not edit.
 *
 * Run \`npm run gen:evidence\` to regenerate from
 * \`tests/fixtures/tools/resolve_evidence.json\`. Parity is asserted in CI.
 */

import type { EvidenceRecord } from "@/lib/evidence";

export const EVIDENCE_ROWS: EvidenceRecord[] = ${JSON.stringify(records, null, 2)};
`;

mkdirSync(dirname(TARGET), { recursive: true });
writeFileSync(TARGET, body, "utf8");
console.log(`wrote ${records.length} evidence rows to ${TARGET}`);
