import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";

import { EvidenceProvider } from "@/components/evidence/EvidenceProvider";
import { SourceHeader } from "@/components/SourceHeader";

import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "WAR ROOM",
  description: "Treasury forecast cycle and crisis-response console",
};

const SCREENS = [
  // Onboarding comes first because it is first: a tenant has a data source before it has
  // a forecast, and the DB Agent is the screen that gives it one.
  { key: "onboarding", label: "Data Source", href: "/onboarding" },
  { key: "forecast", label: "Forecast", href: "/" },
  { key: "agents", label: "Agents", href: "/agents" },
  // The War Room is where the agents are started, so it is always reachable. An escalation
  // is a state of that screen, not a separate destination: an analyst who watched one ten
  // minutes ago should be able to get back to what it concluded.
  { key: "war-room", label: "War Room", href: "/war-room" },
  { key: "recommendation", label: "Recommendation", href: "/recommendation" },
  { key: "approvals", label: "Approvals", href: "/approvals" },
  { key: "evidence", label: "Evidence Explorer", href: "/evidence" },
];

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col bg-surface-0 text-ink">
        {/* Which ledger these screens are showing, on every page. Synthetic data must never
            be mistakable for a tenant's own, and "nothing is loaded" must never be
            mistakable for "loaded and empty". */}
        <SourceHeader />

        <header className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-line px-4 py-3">
          <span className="font-mono text-sm font-semibold tracking-widest text-ink">WAR ROOM</span>
          <nav className="flex gap-4 text-xs" aria-label="Screens">
            {SCREENS.map((screen) =>
              screen.href ? (
                <Link key={screen.key} href={screen.href} className="text-ink hover:text-accent">
                  {screen.label}
                </Link>
              ) : (
                /* The War Room only exists during an escalation, so it is not a place you
                   can navigate to on a quiet Monday. */
                <span key={screen.key} className="text-ink-3" title="Opens during an escalation">
                  {screen.label}
                </span>
              ),
            )}
          </nav>
        </header>

        <EvidenceProvider>
          <main className="flex-1">{children}</main>
        </EvidenceProvider>
      </body>
    </html>
  );
}
