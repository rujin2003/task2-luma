import type { ApprovalCardFixture, ApprovalRoute } from "@/fixtures/warroom";
import { APPROVAL_CARD_FIELDS } from "@/lib/contracts";

import { Traced } from "../evidence/Traced";
import { Panel, StatusTag } from "../primitives";

const STATE_STATUS = {
  draft: "warning",
  pending: "warning",
  approved: "good",
  rejected: "critical",
  blocked: "critical",
  expired: "serious",
} as const;

const STATE_LABEL = {
  draft: "Draft",
  pending: "Awaiting decision",
  approved: "Approved",
  rejected: "Rejected",
  blocked: "Blocked by policy",
  expired: "Expired",
} as const;

/**
 * The eight fields, in the order a treasurer reads them before signing.
 *
 * The order is not a layout choice and this component does not own it: the backend renders
 * `card` as an ordered mapping and `APPROVAL_CARD_FIELDS` pins the sequence on both sides.
 * Iterating the backend's own ordering means a field cannot be dropped or moved here
 * without the contract test noticing.
 *
 * Two fields are the ones that matter and the ones most systems leave blank. WHAT COULD GO
 * WRONG is sourced from the stress run rather than written by hand, and APPROVAL REQUIRED
 * comes from the delegation matrix rather than from whoever is logged in. A card where
 * those two are decorative is a rubber stamp with extra steps.
 */
export function ApprovalCard({ card }: { card: ApprovalCardFixture }) {
  const { request, verdict, state } = card;
  const blocked = state === "blocked";

  return (
    <Panel
      title={`Approval · ${request.approval_required}`}
      subtitle={request.request_id}
      className={blocked ? "border-critical" : ""}
    >
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-line px-4 py-2">
        <StatusTag status={STATE_STATUS[state]} label={STATE_LABEL[state]} />
        <span className="font-mono text-xs text-ink-3">prepared by {request.prepared_by}</span>
        {request.data_snapshot_ref ? (
          /* Which data they were shown, not which data exists now. */
          <span className="font-mono text-xs text-ink-3">snapshot {request.data_snapshot_ref}</span>
        ) : null}
      </div>

      <dl className="divide-y divide-line">
        {APPROVAL_CARD_FIELDS.map((field) => (
          <div key={field} className="grid gap-1 px-4 py-2 sm:grid-cols-[11rem_1fr] sm:gap-4">
            <dt className="text-xs tracking-wide text-ink-3 uppercase">{field}</dt>
            <dd className="text-xs leading-relaxed text-ink-2">
              {field === "EVIDENCE" ? (
                <span className="flex flex-wrap gap-x-3 font-mono text-ink-3">
                  {request.evidence.map((evidence) => (
                    <Traced
                      key={evidence.reference}
                      reference={evidence.reference}
                      label={`Evidence for ${request.action}`}
                    >
                      {evidence.reference}
                    </Traced>
                  ))}
                </span>
              ) : field === "AMOUNT" ? (
                <span className="font-mono text-ink tabular-nums">{card.card[field]}</span>
              ) : field === "ACTION" ? (
                <span className="text-ink">{card.card[field]}</span>
              ) : (
                card.card[field]
              )}
            </dd>
          </div>
        ))}
      </dl>

      {verdict ? (
        <div className="border-t border-line px-4 py-2">
          <p className="text-xs tracking-wide text-ink-3 uppercase">Why this needs a signature</p>
          <p className="mt-1 text-xs leading-relaxed text-ink-2">{verdict.basis}</p>
          <p className="mt-1 font-mono text-xs text-ink-3">
            {verdict.reversibility} · {verdict.counterparty_impact}
          </p>
        </div>
      ) : null}

      {request.route ? <Routing route={request.route} blocked={blocked} /> : null}
    </Panel>
  );
}

/**
 * The delegation band this action fell into, drawn as the chain it is.
 *
 * A blocked route renders with no approver at all rather than with a very senior one.
 * That distinction is the whole control: payroll and statutory tax are outside the matrix,
 * and a screen that showed "approval required: board" would invite somebody to go and find
 * a board member.
 */
function Routing({ route, blocked }: { route: ApprovalRoute; blocked: boolean }) {
  return (
    <div className="border-t border-line px-4 py-2">
      <p className="text-xs tracking-wide text-ink-3 uppercase">Routing · {route.route_id}</p>
      {blocked ? (
        <p className="mt-1 text-xs leading-relaxed text-critical">
          ✗ No delegation level can authorise this. {route.blocked_reason}
        </p>
      ) : (
        <p className="mt-1 flex flex-wrap items-center gap-2 font-mono text-xs text-ink-2">
          <Step label="prepares" who={route.responsible} />
          <span aria-hidden className="text-ink-3">
            →
          </span>
          <Step label="reviews" who={route.reviewer} />
          <span aria-hidden className="text-ink-3">
            →
          </span>
          <Step label="signs" who={route.accountable} highlight />
          {route.informed.length > 0 ? (
            <span className="text-ink-3">· informed: {route.informed.join(", ")}</span>
          ) : null}
        </p>
      )}
    </div>
  );
}

function Step({
  label,
  who,
  highlight = false,
}: {
  label: string;
  who?: string | null;
  highlight?: boolean;
}) {
  return (
    <span className={highlight ? "text-ink" : "text-ink-2"}>
      {who ?? "—"}
      <span className="text-ink-3"> {label}</span>
    </span>
  );
}
