import { EvidenceExplorer } from "@/components/evidence/EvidenceExplorer";

/**
 * The Evidence Explorer, as a screen of its own.
 *
 * Built early on purpose: it is the debugging tool for everything else. When a number on
 * the Forecast screen looks wrong, this is where you find out whether the number is wrong
 * or the row underneath it is -- and that question comes up constantly while the engine
 * and the agents are still being wired together.
 */
export default function EvidenceScreen() {
  return <EvidenceExplorer />;
}
