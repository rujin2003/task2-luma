"""Walk the DB Agent end to end on the command line.

The screen is the product; this is for the times you want the same eight stages without
a browser — a smoke test after a deploy, or an argument about why a mapping was rejected.
It prints exactly what the UI shows, in the same order, and exits non-zero when the
reconciliation gate refuses. A CI job can therefore assert "this schema still maps".
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.api import store
from backend.ingest.onboarding import Onboarding
from backend.ingest.validate import BalanceSnapshot
from scripts.demo_company import BY_KEY, MANIFEST, build_all, major


def _manifest(directory: Path, tenant: str) -> dict[str, object]:
    if not MANIFEST.exists():
        build_all(directory)
    catalog = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for entry in catalog["companies"]:
        if entry["key"] == tenant:
            return entry
    raise SystemExit(f"no demo tenant {tenant!r}; try one of {', '.join(sorted(BY_KEY))}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default="helios", choices=sorted(BY_KEY))
    parser.add_argument("--dir", default="var/demo")
    arguments = parser.parse_args()

    entry = _manifest(Path(arguments.dir), arguments.tenant)
    onboarding = Onboarding(
        tenant_id=str(entry["key"]),
        company_name=str(entry["company"]),
        default_currency=str(entry["currency"]),
    )

    probe = onboarding.connect(str(entry["url"]))
    print(f"\n{entry['company']}  —  {entry['schema_style']}")
    print(f"  1 connect     {onboarding.redacted_url}")
    print(
        f"                {probe.dialect} {probe.server_version}, {probe.table_count} tables, "
        f"write {'granted' if probe.can_write else 'not granted'}"
    )

    fingerprint = onboarding.introspect()
    print(f"  2 introspect  fingerprint {fingerprint.fingerprint_hash[:16]}")

    proposal = onboarding.classify()
    print(f"  3 classify    {len(proposal.tables)} tables, {onboarding.model_calls} model calls")
    for row in sorted(proposal.tables, key=lambda item: item.entity_role):
        fields = [c.canonical_field for c in proposal.columns if c.table == row.table]
        print(
            f"                {row.entity_role:<16} <- {row.table:<22} "
            f"{row.confidence_bps / 100:.0f}%  ({len(fields)} fields)"
        )
    if onboarding.residue and onboarding.residue.tables:
        print(f"                residue: {', '.join(onboarding.residue.tables)}")

    mapping = onboarding.draft()
    print(f"  4 map         {len(mapping.entities)} entities")

    controls = entry["control_balances"]
    assert isinstance(controls, dict)
    stated = BalanceSnapshot(
        ar_minor=controls["ar_control_minor"],
        ap_minor=controls["ap_control_minor"],
        cash_minor=controls["cash_control_minor"],
        currency=controls["currency"],
    )

    with store.session() as target:
        report = onboarding.load(target, stated=stated)
        reconciliation = onboarding.reconciliation
        accepted = reconciliation is not None and reconciliation.accepted
        if accepted:
            target.commit()
        else:
            target.rollback()

    print(f"  5 reconcile   {'accepted' if accepted else 'REJECTED'}")
    if reconciliation is not None:
        for failure in reconciliation.failures:
            print(f"                ✗ {failure}")
    print(f"  6 freeze      {onboarding.mapping_path or '— not frozen'}")
    print(f"  7 load        {report.mapped_rows} rows of {report.source_rows}")
    for entity, count in report.counts.items():
        print(f"                {entity:<16} {count:>6}")
    print(
        f"                AR {major(report.open_invoices_minor)}  "
        f"AP {major(report.open_vendor_invoices_minor)}  "
        f"cash {major(report.bank_transactions_minor)} {report.currency}"
    )
    for assumption in sorted(set(report.assumptions)):
        print(f"                ⚠ {assumption}")
    for reject in report.rejects[:10]:
        print(f"                ✗ {reject.entity} {reject.source_key}: {reject.reason}")

    drift = onboarding.check_drift()
    print(f"  8 drift       {'changed — ingestion held' if drift.changed else 'no change'}\n")

    return 0 if accepted else 1


if __name__ == "__main__":
    sys.exit(main())
