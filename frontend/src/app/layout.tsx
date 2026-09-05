import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
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
  { key: "forecast", label: "Forecast", active: true },
  { key: "war-room", label: "War Room", active: false },
  { key: "recommendation", label: "Recommendation", active: false },
  { key: "evidence", label: "Evidence Explorer", active: false },
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
            {SCREENS.map((screen) => (
              <span
                key={screen.key}
                className={screen.active ? "text-ink" : "text-ink-3"}
                aria-current={screen.active ? "page" : undefined}
              >
                {screen.label}
              </span>
            ))}
          </nav>
        </header>

        <main className="flex-1">{children}</main>
      </body>
    </html>
  );
}
