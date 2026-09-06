"""The golden path, narrated.

    python -m scripts.demo
    python -m scripts.demo --break-llm      # the same Monday with no model available

Runs the real product -- the same `Session` the API serves -- and prints what it did. It
is a demo script rather than a script that fakes a demo: nothing here computes a figure,
chooses a plan or decides an approval. Everything printed came out of the orchestrator,
and if the orchestrator stops producing it this script prints less rather than pretending.

`--break-llm` exists because the interesting question about an agentic system is not what
it does when the model answers. Run it and the cycle still publishes, the war room still
opens on the same breach, the Commander still picks a named plan, and every one of those
falls back deterministically and says so. That is the demo worth giving.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from backend.agents.fake import FakeProvider
from backend.api.session import Session
from backend.contracts import AgentRole
from backend.contracts.approvals import ApprovalDecision, ApprovalRole
from backend.orchestrator.cycle import CYCLE_STEPS

RECORDINGS = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "llm"

TREASURER = "treasurer@novatech"
CFO = "cfo@novatech"

RULE = "─" * 78


def head(title: str) -> None:
    print(f"\n{RULE}\n  {title}\n{RULE}")


def line(mark: str, text: str) -> None:
    print(f"  {mark} {text}")


def blackout_provider(root: Path) -> FakeProvider:
    """Every recording replaced by an injected timeout."""
    shutil.copytree(RECORDINGS, root, ignore=shutil.ignore_patterns("_*", "__*"))
    for role in AgentRole:
        payload = [{"agent": role.value, "default": True, "raises": "timeout"}]
        (root / f"{role.value}.json").write_text(json.dumps(payload), encoding="utf-8")
    return FakeProvider(root)


async def demo(session: Session) -> None:
    head("1-7  The Monday cycle")
    cycle = await session.run_cycle()
    for step in cycle.steps_completed:
        line("✓", step)
    line("•", f"version {cycle.forecast_version_id}, as of {cycle.as_of}")
    print()
    for row in cycle.bridge:
        if not row.material:
            continue
        mark = "✓" if row.explanation else "⚠"
        line(mark, f"{row.category}: {row.delta} — {row.explanation or row.unexplained_reason}")
    line("•", f"immaterial rows: {cycle.immaterial_basis}")
    if cycle.degraded:
        print()
        for reason in cycle.degradation_reasons:
            line("⚠", reason)

    head("8-9  Review, then publish")
    line("•", f"steps 8 and 9 are the human's; {CYCLE_STEPS[7]} happens off this screen")
    published = session.publish(published_by=TREASURER, published_by_role=ApprovalRole.TREASURER)
    line("✓", f"{published.version_id} published by {published.published_by}")
    line("•", f"prepared by {published.prepared_by} — preparer is never publisher")

    head("10  Policy check")
    check = await session.check_policy()
    for violation in check.violations:
        line("✗" if violation.severity.value == "hard" else "⚠", violation.display())
    if not check.escalate:
        line("✓", "within policy — no war room today")
        return

    head("The war room")
    investigation = await session.open_war_room()
    line("•", f"plan: {investigation.plan_id} — {investigation.plan_rationale}")
    for skip in ():  # plan skips travel on the event, not the result
        line("•", str(skip))
    for run in investigation.runs:
        mark = "✓" if run.status.value == "complete" else "⚠"
        line(mark, f"{run.agent.value}: {run.finding.headline if run.finding else run.status.value}")

    if investigation.conflicts:
        print()
        for conflict_id in investigation.conflicts:
            line("⚠", f"conflict {conflict_id} — detected deterministically, resolved by evidence")

    recommendation = investigation.recommendation
    if recommendation is None:
        line("✗", "the investigation produced no recommendation")
        return

    if recommendation.replan_history:
        print()
        for attempt in recommendation.replan_history:
            line("↻", f"attempt {attempt.attempt} ({attempt.strategy_id}): {attempt.failure_reason}")

    head("The worklist")
    for item in recommendation.worklist:
        line(
            "•",
            f"{item.seq}. {item.action} — {item.owner}, {item.amount}, due {item.due_date}",
        )

    if recommendation.rejected_actions:
        print()
        for rejection in recommendation.rejected_actions:
            line("✗", f"refused by {rejection.rejected_by}: {rejection.reason}")

    head("Stress")
    for result in recommendation.stress_results:
        mark = "✓" if result.passed else "✗"
        line(
            mark,
            f"{result.strategy_id} under {result.stressor.label} "
            f"({result.stressor.shift_pct}%): {result.min_cash} at W{result.min_cash_week}",
        )
    print()
    for stressor in {r.stressor.stressor_id: r.stressor for r in recommendation.stress_results}.values():
        line("•", f"{stressor.label}: {stressor.calibration}")

    head("Approvals")
    pack = await session.approval_pack()
    for request in pack.requests:
        print()
        for field, value in request.card().items():
            print(f"  {field:<20} {value}")

    if not pack.requests:
        line("•", "nothing needed a signature")
        return

    print()
    request = pack.requests[0]
    role = request.approval_required
    signer = CFO if role is ApprovalRole.CFO else TREASURER

    try:
        session.decide(
            ApprovalDecision(
                request_id=request.request_id,
                decided_by=session.prepared_by,  # the preparer, deliberately
                decided_by_role=role,
                approved=True,
                decided_at=published.published_at,
            )
        )
    except Exception as exc:
        line("✗", f"preparer tried to sign their own card: {exc}")

    entry = session.decide(
        ApprovalDecision(
            request_id=request.request_id,
            decided_by=signer,
            decided_by_role=role,
            approved=True,
            decided_at=published.published_at,
        )
    )
    line("✓", f"signed by {entry.actor} ({entry.actor_role.value}) against {entry.data_snapshot_ref}")

    row = next(r for r in pack.worklist if r.seq == request.worklist_seq)
    line("•", await session.execute(row.seq))


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--break-llm",
        action="store_true",
        help="inject a timeout into every model call and run the same Monday",
    )
    args = parser.parse_args()

    if args.break_llm:
        with tempfile.TemporaryDirectory() as tmp:
            provider = blackout_provider(Path(tmp) / "llm")
            print("\n  Running with every model call timing out.")
            await demo(Session(provider=provider))
    else:
        await demo(Session())

    print()


if __name__ == "__main__":
    asyncio.run(main())
