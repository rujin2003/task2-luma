"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { getDataSource, type DataSourceStatus } from "@/lib/position";

/**
 * The persistent banner: which ledger every screen below is currently showing.
 *
 * It is not decoration. This product renders a tenant's real financial position and a
 * recorded synthetic one through exactly the same components, and the only thing standing
 * between those two on a screenshot is this line — so it states the source, its kind and
 * its as-of date on every page, and says plainly when nothing is loaded at all.
 *
 * Re-read on navigation rather than polled: the things that change it (an onboarding load,
 * a reset) are all followed by a navigation or a reload in this app.
 */
export function SourceHeader() {
  const pathname = usePathname();
  const [status, setStatus] = useState<DataSourceStatus | null>(null);

  useEffect(() => {
    getDataSource()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, [pathname]);

  const source = status?.source;

  if (!source) {
    return (
      <div className="bg-surface-2 px-4 py-1 text-center text-xs text-ink-2">
        No data source loaded —{" "}
        <Link href="/onboarding" className="text-accent underline-offset-2 hover:underline">
          load a tenant database
        </Link>{" "}
        to fill these screens.
      </div>
    );
  }

  if (source.kind === "demo") {
    return (
      <div className="bg-warning px-4 py-1 text-center text-xs font-medium text-surface-0">
        Synthetic demo data — {source.company}, as of {source.as_of}. Not a production ledger.
      </div>
    );
  }

  return (
    <div className="bg-surface-2 px-4 py-1 text-center text-xs text-ink-2">
      <span className="font-medium text-ink">{source.company}</span> — loaded from the
      tenant&rsquo;s own database, as of {source.as_of} · {source.currency}
      {source.reconciled ? " · reconciled to trial balance" : ""}
    </div>
  );
}
