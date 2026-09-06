import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";

import { EvidenceProvider } from "@/components/evidence/EvidenceProvider";

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
  { key: "forecast", label: "Forecast", href: "/" },
  // The War Room is reachable because an escalation is open on the recorded golden path.
  // When the session is quiet the screen renders "no war room is open" rather than being
  // removed from the nav: an analyst who watched one ten minutes ago should be able to get
  // back to what it concluded, not wonder whether they imagined it.
  { key: "war-room", label: "War Room", href: "/war-room" },
  { key: "recommendation", label: "Recommendation", href: "/recommendation" },
  { key: "approvals", label: "Approvals", href: "/approvals" },
  { key: "evidence", label: "Evidence Explorer", href: "/evidence" },
];

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col bg-surface-0 text-ink">
        {/* Synthetic data must never be mistakable for production. This banner is persistent. */}
        <div className="bg-warning px-4 py-1 text-center text-xs font-medium text-surface-0">
          Synthetic demo data — NovaTech Industries. Not a production ledger.
        </div>

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
