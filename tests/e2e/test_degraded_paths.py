"""What the product does when the parts it depends on stop working.

The unit tests already cover each failure in isolation: a timeout, a schema violation, a
tool that will not answer, a citation that does not resolve. What they cannot tell you is
whether the *product* still works, and that is the only question a treasurer has. A system
that degrades correctly at every layer and then hands back nothing has not degraded
correctly.

So every test here injects a failure into the recorded provider, runs the whole Monday
over HTTP, and asserts two things together:

* an answer still comes out -- a published version, a worklist, a card to sign;
* the loss is *stated* -- named on the response, streamed as a degraded event, and
  therefore renderable as a first-class UI state rather than a toast that scrolls away.

The second half is what stops the first half from being a lie. An investigation that
quietly proceeds without Supplier Risk and presents a confident deferral is worse than one
that fails outright, because nobody can see which one they are looking at.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.agents.fake import FakeProvider
from backend.api.session import Session, reset_session
from backend.contracts import AgentRole
from backend.main import create_app
from tests.e2e.conftest import CFO, TREASURER

RECORDINGS = Path(__file__).resolve().parents[1] / "fixtures" / "llm"

# The parallel wave is meant to finish in about a minute on a throttled free tier. Against
# the replay provider it is effectively instant, so this is a regression bound rather than
# a benchmark: it catches an accidental `await` in series, which is the realistic way this
# number goes from seconds to minutes.
WAVE_BUDGET_S = 60.0


def injected(tmp_path: Path, failures: dict[AgentRole, str]) -> FakeProvider:
    """The real recordings, with one role's answers replaced by a failure."""
    root = tmp_path / "llm"
    shutil.copytree(RECORDINGS, root, ignore=shutil.ignore_patterns("_*", "__*"))
    for role, mode in failures.items():
        payload = [{"agent": role.value, "default": True, "raises": mode}]
        (root / f"{role.value}.json").write_text(json.dumps(payload), encoding="utf-8")
    return FakeProvider(root)


async def drive(provider: FakeProvider):
    """A client over a session wired to a deliberately broken provider."""
    reset_session(Session(provider=provider))
    transport = ASGITransport(app=create_app())
    return AsyncClient(transport=transport, base_url="http://warroom.test")


async def run_monday(client) -> dict:
    """Cycle, publish, check, escalate. Returns the investigation, however it went."""
    await client.post("/api/cycle/run")
    await client.post(
        "/api/cycle/publish",
        json={"published_by": TREASURER, "published_by_role": "treasurer"},
    )
    check = await client.post("/api/cycle/policy-check")
    assert check.json()["escalate"]
    opened = await client.post("/api/war-room/open")
    assert opened.status_code == 200, opened.text
    return opened.json()


def degraded_events(history: dict) -> list[dict]:
    return [event for event in history["events"] if event["type"] == "system.degraded"]


# --- the LLM is down --------------------------------------------------------------------


@pytest.fixture
async def blackout(tmp_path):
    """Every model call times out. The most likely free-tier Monday there is."""
    provider = injected(
        tmp_path,
        {role: "timeout" for role in AgentRole},
    )
    async with await drive(provider) as client:
        yield client


async def test_the_cycle_still_publishes_when_no_model_answers(blackout) -> None:
    """The forecast is Person 1's engine. No agent computes it, so none can block it."""
    response = await blackout.post("/api/cycle/run")

    assert response.status_code == 200
    body = response.json()
    assert body["forecast_version_id"] == "fv-2026-W10"
    assert body["degraded"], "a cycle that lost its explanations must say so"
    assert any("variance" in reason.lower() for reason in body["degradation_reasons"])


async def test_the_variance_bridge_still_ties_without_its_explanations(blackout) -> None:
    """The numbers are the engine's; only the prose was the model's."""
    body = (await blackout.post("/api/cycle/run")).json()

    material = [row for row in body["bridge"] if row["material"]]
    assert material, "the bridge is still built"
    assert all(row["unexplained_reason"] for row in material), (
        "an unexplained material row must say why it is unexplained"
    )


async def test_a_total_blackout_still_produces_a_recommendation(blackout) -> None:
    """Every lever the Commander would have chosen has a deterministic fallback."""
    investigation = await run_monday(blackout)

    assert investigation["phase"] == "closed"
    assert investigation["recommendation"] is not None
    assert investigation["degraded"]
    assert investigation["degradation_reasons"]


async def test_the_commander_falls_back_to_a_named_plan_and_says_why(blackout) -> None:
    investigation = await run_monday(blackout)

    assert investigation["plan_id"], "a fallback plan is still a plan with an id"
    assert "Default plan" in investigation["plan_rationale"]
    history = (await blackout.get("/api/war-room/history")).json()
    assert any(
        event["type"] == "system.degraded" and event["component"] == "commander"
        for event in history["events"]
    )


async def test_no_agent_lane_is_left_spinning(blackout) -> None:
    """Every lane the stream opens, the stream closes -- including the choosers."""
    await run_monday(blackout)
    events = (await blackout.get("/api/war-room/history")).json()["events"]

    started = {(e["agent"], e["run_id"]) for e in events if e["type"] == "agent.started"}
    settled = {(e["agent"], e["run_id"]) for e in events if e["type"] == "agent.status"}

    assert started, "the wave ran"
    assert started <= settled, f"never settled: {sorted(started - settled)}"


async def test_the_degradation_is_streamed_not_only_returned(blackout) -> None:
    """The UI renders degradation as a state, which means it has to arrive as an event."""
    await run_monday(blackout)

    history = (await blackout.get("/api/war-room/history")).json()

    assert degraded_events(history), "a degraded run that streams nothing is a silent one"
    assert all(event["mark"] == "warn" for event in degraded_events(history))
    assert all(event["reason"] for event in degraded_events(history))


# --- one adversary is missing -------------------------------------------------------------


@pytest.fixture
async def no_supplier_risk(tmp_path):
    """The agent whose whole job is to reject other agents' proposals is gone."""
    async with await drive(injected(tmp_path, {AgentRole.SUPPLIER_RISK: "timeout"})) as client:
        yield client


async def test_losing_the_adversary_is_visible_in_the_result(no_supplier_risk) -> None:
    """Silently accepting every AP deferral is the failure this must not have."""
    investigation = await run_monday(no_supplier_risk)

    assert investigation["degraded"]
    assert any("supplier_risk" in reason for reason in investigation["degradation_reasons"]), (
        investigation["degradation_reasons"]
    )


async def test_the_timed_out_agent_reports_a_terminal_status_with_a_reason(
    no_supplier_risk,
) -> None:
    await run_monday(no_supplier_risk)
    events = (await no_supplier_risk.get("/api/war-room/history")).json()["events"]

    statuses = [
        event
        for event in events
        if event["type"] == "agent.status" and event["agent"] == "supplier_risk"
    ]
    assert statuses, "an agent that vanished is not a status the UI can draw"
    assert statuses[-1]["status"] in {"timeout", "failed", "degraded"}
    assert statuses[-1]["failure_reason"]


async def test_the_worklist_survives_a_missing_agent(no_supplier_risk) -> None:
    investigation = await run_monday(no_supplier_risk)

    assert investigation["recommendation"]["worklist"], "there is still work to do Monday"


# --- a model that answers, but wrongly -----------------------------------------------------


@pytest.fixture
async def malformed(tmp_path):
    """The AR agent returns something that is not a finding."""
    async with await drive(injected(tmp_path, {AgentRole.AR_COLLECTIONS: "schema_violation"})) as (
        client
    ):
        yield client


async def test_a_malformed_answer_is_not_a_finding(malformed) -> None:
    """A response that does not validate is discarded, never coerced into shape."""
    investigation = await run_monday(malformed)
    events = (await malformed.get("/api/war-room/history")).json()["events"]

    findings = [
        event
        for event in events
        if event["type"] == "agent.finding" and event["agent"] == "ar_collections"
    ]
    assert not findings
    assert investigation["degraded"]


async def test_the_investigation_still_closes_on_a_malformed_answer(malformed) -> None:
    investigation = await run_monday(malformed)

    assert investigation["phase"] == "closed"
    assert investigation["recommendation"] is not None


# --- the wave is parallel ------------------------------------------------------------------


async def test_the_parallel_wave_finishes_inside_its_budget(client, published) -> None:
    """Bounded concurrency, not serial awaits. The demo depends on this being true."""
    await client.post("/api/cycle/policy-check")

    investigation = (await client.post("/api/war-room/open")).json()

    assert investigation["elapsed_ms"] < WAVE_BUDGET_S * 1000
    runs = (await client.get("/api/war-room")).json()["runs"]
    assert len(runs) >= 6, "the whole wave ran, so the bound is measured over all of it"


async def test_the_recorded_provider_makes_the_run_repeatable(client, published) -> None:
    """Byte-identical golden path: same fixtures in, same recommendation out."""
    await client.post("/api/cycle/policy-check")
    first = (await client.post("/api/war-room/open")).json()

    reset_session()
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://warroom.test") as second_client:
        second = await run_monday(second_client)

    assert first["recommendation"]["summary"] == second["recommendation"]["summary"]
    assert [row["action"] for row in first["recommendation"]["worklist"]] == [
        row["action"] for row in second["recommendation"]["worklist"]
    ]


# --- the controls do not relax under degradation ---------------------------------------------


async def test_a_degraded_run_still_cannot_be_self_approved(no_supplier_risk) -> None:
    """The interesting failure: controls that hold on the happy path and slip on the sad one."""
    await run_monday(no_supplier_risk)
    board = (await no_supplier_risk.get("/api/approvals")).json()
    card = next(c for c in board["cards"] if c["state"] == "pending")

    response = await no_supplier_risk.post(
        f"/api/approvals/{card['request']['request_id']}/decide",
        json={
            "decided_by": "analyst@novatech",
            "decided_by_role": card["request"]["approval_required"],
            "approved": True,
        },
    )

    assert response.status_code == 403


async def test_a_degraded_run_still_gates_execution(no_supplier_risk) -> None:
    await run_monday(no_supplier_risk)
    board = (await no_supplier_risk.get("/api/approvals")).json()
    card = next(c for c in board["cards"] if c["state"] == "pending")

    response = await no_supplier_risk.post(
        f"/api/approvals/worklist/{card['request']['worklist_seq']}/execute"
    )

    assert response.status_code == 403
    assert "not approved" in response.json()["detail"]


async def test_a_card_from_a_degraded_run_still_states_what_could_go_wrong(
    no_supplier_risk,
) -> None:
    """The field is sourced from the stress run, which still happened."""
    await run_monday(no_supplier_risk)
    board = (await no_supplier_risk.get("/api/approvals")).json()

    for card in board["cards"]:
        assert card["card"]["WHAT COULD GO WRONG"]
        assert card["card"]["EVIDENCE"]


async def test_a_degraded_run_still_signs_cleanly_with_the_right_person(
    no_supplier_risk,
) -> None:
    await run_monday(no_supplier_risk)
    board = (await no_supplier_risk.get("/api/approvals")).json()
    card = next(c for c in board["cards"] if c["request"]["approval_required"] == "cfo")

    response = await no_supplier_risk.post(
        f"/api/approvals/{card['request']['request_id']}/decide",
        json={"decided_by": CFO, "decided_by_role": "cfo", "approved": True},
    )

    assert response.status_code == 200
    assert response.json()["card_seen"] == card["card"]
