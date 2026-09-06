"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { EvidenceDrawer } from "./EvidenceDrawer";

interface EvidenceContextValue {
  /** The reference currently being walked, or null when the drawer is closed. */
  reference: string | null;
  open: (reference: string) => void;
  close: () => void;
}

const EvidenceContext = createContext<EvidenceContextValue | null>(null);

/**
 * Holds the one piece of state the Forecast screen needs to be interactive: which number
 * the analyst is currently pulling on.
 *
 * It lives at the layout so every screen shares it. The Evidence Explorer is meant to be
 * reachable from any number anywhere, and a per-screen drawer would quietly become three
 * slightly different drawers.
 */
export function EvidenceProvider({ children }: { children: ReactNode }) {
  const [reference, setReference] = useState<string | null>(null);

  const open = useCallback((next: string) => setReference(next), []);
  const close = useCallback(() => setReference(null), []);
  const value = useMemo(() => ({ reference, open, close }), [reference, open, close]);

  return (
    <EvidenceContext.Provider value={value}>
      {children}
      <EvidenceDrawer reference={reference} onClose={close} onWalk={open} />
    </EvidenceContext.Provider>
  );
}

export function useEvidence(): EvidenceContextValue {
  const context = useContext(EvidenceContext);
  if (!context) {
    throw new Error("useEvidence must be used inside an EvidenceProvider");
  }
  return context;
}
