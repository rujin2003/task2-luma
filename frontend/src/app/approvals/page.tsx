import { ApprovalCard } from "@/components/approvals/ApprovalCard";
import { Panel } from "@/components/primitives";
import { RejectedActions } from "@/components/recommendation/RejectedActions";
import { Worklist } from "@/components/recommendation/Worklist";
import { approvalsFixture } from "@/fixtures/warroom";

/**
 * The signing surface: every row that needs a human, and every row that does not.
 *
 * The auto-safe rows are shown alongside the gated ones on purpose. A screen that listed
 * only what needs approving would leave the analyst unable to answer "what is actually
 * happening this week", and the reversible rows — a collection call, a Dodo retry — are
 * most of the work even though none of them will ever be signed.
 */
export default function ApprovalsScreen() {
  const { cards, worklist, rejected } = approvalsFixture;
  const gated = cards.filter((card) => card.state !== "blocked");
  const blocked = cards.filter((card) => card.state === "blocked");
  const autoSafe = worklist.filter((row) => row.status === "queued");

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-4 px-4 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold text-ink">Approvals</h1>
        <p className="font-mono text-xs text-ink-3">
          {gated.length} awaiting signature · {autoSafe.length} auto-safe · {blocked.length} blocked
        </p>
      </div>

      <Panel title="Execution mode" subtitle="dry run">
        <p className="px-4 py-3 text-xs leading-relaxed text-ink-2">
          Every row executes against a dry-run adapter: approving here records the decision and the
          card that was signed, and moves no money. Nothing is executed without a recorded,
          matching, maker-checker-clean approval, and the preparer of a card can never be the person
          who signs it.
        </p>
      </Panel>

      <div className="grid gap-4 xl:grid-cols-2">
        {gated.map((card) => (
          <ApprovalCard key={card.request.request_id} card={card} />
        ))}
      </div>

      {blocked.length > 0 ? (
        <>
          <h2 className="mt-2 text-sm font-semibold tracking-wide text-critical uppercase">
            Blocked by policy
          </h2>
          <div className="grid gap-4 xl:grid-cols-2">
            {blocked.map((card) => (
              <ApprovalCard key={card.request.request_id} card={card} />
            ))}
          </div>
        </>
      ) : null}

      <Worklist items={worklist} />

      <RejectedActions rejected={rejected} />
    </div>
  );
}
