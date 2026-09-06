"""The golden path over HTTP, and the refusals that guard each step of it.

Monday, in one file: refresh, review, publish, check policy, open the war room on the
breach it found, read the worklist, sign one card and execute the row it authorised.

The order matters more than any single assertion. Almost every test here has a negative
twin -- the step done too early, or by the wrong person -- because a control that only
works when the caller is well-behaved is not a control.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.api.warroom import events
from backend.orchestrator.cycle import CYCLE_STEPS, LAST_AUTOMATED_STEP
from tests.e2e.conftest import ANALYST, CFO, TREASURER


async def test_health_reports_a_version(client) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# --- the order is the product -----------------------------------------------------------


async def test_publishing_before_the_cycle_runs_is_refused(client) -> None:
    response = await client.post(
        "/api/cycle/publish",
        json={"published_by": TREASURER, "published_by_role": "treasurer"},
    )

    assert response.status_code == 409
    assert response.json()["error"] == "out_of_order"
    assert "no cycle has been run" in response.json()["detail"]


async def test_the_policy_check_will_not_run_against_a_draft(client) -> None:
    """A war room opened on a version nobody signed is a war room nobody trusts."""
    await client.post("/api/cycle/run")

    response = await client.post("/api/cycle/policy-check")

    assert response.status_code == 409
    assert "publish first" in response.json()["detail"]


async def test_the_war_room_will_not_open_without_a_policy_check(client, published) -> None:
    response = await client.post("/api/war-room/open")

    assert response.status_code == 409
    assert "opens on a policy check" in response.json()["detail"]


async def test_approvals_need_a_recommendation_to_approve(client) -> None:
    response = await client.get("/api/approvals")

    assert response.status_code == 409
    assert "recommendation" in response.json()["detail"]


# --- steps 1-7 ----------------------------------------------------------------------------


async def test_the_automated_half_stops_at_the_review_gate(client) -> None:
    response = await client.post("/api/cycle/run")

    assert response.status_code == 200
    body = response.json()
    assert body["steps_completed"] == list(CYCLE_STEPS[:LAST_AUTOMATED_STEP])
    assert "Review & challenge" not in body["steps_completed"], "step 8 is the human's"
    assert body["forecast_version_id"] == "fv-2026-W10"
    assert not body["degraded"], body["degradation_reasons"]


async def test_only_material_rows_carry_an_explanation(client) -> None:
    """Explaining a $30K variance signals you have never done this job."""
    body = (await client.post("/api/cycle/run")).json()

    material = [row for row in body["bridge"] if row["material"]]
    immaterial = [row for row in body["bridge"] if not row["material"]]

    assert material and immaterial
    assert all(row["explanation"] for row in material)
    assert all(row["evidence"] == [] for row in immaterial)


async def test_the_exceptions_queue_states_its_materiality_basis(client) -> None:
    await client.post("/api/cycle/run")

    body = (await client.get("/api/cycle/exceptions")).json()

    assert body["exceptions"], "the seeded Monday surfaces exceptions"
    assert body["immaterial_basis"], "a queue that hides rows must say what it hid"


# --- step 8: review and challenge ---------------------------------------------------------


async def test_an_override_is_recorded_with_its_reason(client) -> None:
    await client.post("/api/cycle/run")

    response = await client.post(
        "/api/cycle/override",
        json={
            "target_ref": "forecast:driver-ar-dso",
            "field": "dso_days",
            "previous_value": "47",
            "new_value": "52",
            "reason": "Fabrikam confirmed a 30-day slip on the renewal; the driver is stale",
            "author": TREASURER,
            "author_role": "treasurer",
        },
    )

    assert response.status_code == 200
    assert response.json()["reason"].startswith("Fabrikam confirmed")
    status = (await client.get("/api/cycle")).json()
    assert len(status["overrides"]) == 1


async def test_an_override_that_changes_nothing_is_rejected(client) -> None:
    await client.post("/api/cycle/run")

    response = await client.post(
        "/api/cycle/override",
        json={
            "target_ref": "forecast:driver-ar-dso",
            "field": "dso_days",
            "previous_value": "47",
            "new_value": "47",
            "reason": "no change",
            "author": TREASURER,
            "author_role": "treasurer",
        },
    )

    assert response.status_code == 422


# --- step 9: publish ----------------------------------------------------------------------


async def test_the_preparer_cannot_publish_their_own_cycle(client) -> None:
    await client.post("/api/cycle/run")

    response = await client.post(
        "/api/cycle/publish",
        json={"published_by": ANALYST, "published_by_role": "treasurer"},
    )

    assert response.status_code == 403
    assert response.json()["error"] == "segregation_of_duties"
    assert "cannot also publish" in response.json()["detail"]


async def test_an_analyst_may_not_publish_at_all(client) -> None:
    await client.post("/api/cycle/run")

    response = await client.post(
        "/api/cycle/publish",
        json={"published_by": "someone.else@novatech", "published_by_role": "analyst"},
    )

    assert response.status_code == 403
    assert "may not publish" in response.json()["detail"]


async def test_publishing_twice_is_refused(client, published) -> None:
    response = await client.post(
        "/api/cycle/publish",
        json={"published_by": CFO, "published_by_role": "cfo"},
    )

    assert response.status_code == 409
    assert "already published" in response.json()["detail"]


async def test_the_cycle_cannot_be_re_run_over_a_signed_version(client, published) -> None:
    response = await client.post("/api/cycle/run")

    assert response.status_code == 409
    assert "has been signed" in response.json()["detail"]


# --- step 10: policy check ----------------------------------------------------------------


async def test_the_breach_is_specific_dated_and_quantified(client, published) -> None:
    response = await client.post("/api/cycle/policy-check")

    assert response.status_code == 200
    body = response.json()
    assert body["escalate"]
    worst = body["worst"]
    assert worst["severity"] == "hard"
    assert worst["week_index"] == 6, "the floor breaks at W6 on the seeded data"
    assert worst["observed_money"]["minor_units"] == 1_840_000_000
    assert worst["evidence"], "a breach with no evidence is an opinion"


# --- the war room -------------------------------------------------------------------------


async def test_the_investigation_closes_with_a_recommendation(client, escalated) -> None:
    assert escalated["phase"] == "closed"
    assert escalated["recommendation"] is not None
    assert escalated["plan_id"], "the Commander selects from an enumerated set of plans"


async def test_the_recommendation_carries_the_attempt_that_failed(client, escalated) -> None:
    """Deleting the failed attempt deletes the most persuasive sentence in the output."""
    body = (await client.get("/api/war-room/recommendation")).json()

    assert body["replan_history"], "the cheap bundle does not survive our own 90th percentile"
    attempt = body["replan_history"][0]
    assert attempt["strategy_id"] == "financing-bridge"
    assert "below the" in attempt["failure_reason"]


async def test_rejected_actions_are_surfaced_with_a_reason(client, escalated) -> None:
    """An experienced treasurer looks for this first."""
    body = (await client.get("/api/war-room/recommendation")).json()

    assert body["rejected_actions"], "supplier risk refuses a lever on the golden path"
    rejection = body["rejected_actions"][0]
    assert rejection["reason"]
    assert rejection["evidence"] or rejection["violation"]


async def test_the_stress_results_are_calibrated_not_arbitrary(client, escalated) -> None:
    body = (await client.get("/api/war-room/recommendation")).json()

    assert body["stress_results"]
    calibrations = {r["stressor"]["calibration"] for r in body["stress_results"]}
    assert all(text for text in calibrations), "a stressor must say where its size came from"
    assert any("percentile" in text for text in calibrations)


# --- the live stream ----------------------------------------------------------------------


async def test_the_history_replays_the_cycle_steps_in_order(client) -> None:
    await client.post("/api/cycle/run")

    body = (await client.get("/api/war-room/history")).json()

    steps = [e["step"] for e in body["events"] if e["type"] == "cycle.step"]
    assert steps == list(range(1, LAST_AUTOMATED_STEP + 1))
    assert body["next_seq"] == len(body["events"])


async def test_history_resumes_from_a_cursor(client) -> None:
    await client.post("/api/cycle/run")
    everything = (await client.get("/api/war-room/history")).json()["events"]

    tail = (await client.get("/api/war-room/history", params={"since": 3})).json()["events"]

    assert [e["seq"] for e in tail] == [e["seq"] for e in everything if e["seq"] >= 3]


async def test_the_stream_carries_no_chain_of_thought(client, escalated) -> None:
    """There is no field for a model's reasoning anywhere in the schema, and none here."""
    events = (await client.get("/api/war-room/history")).json()["events"]

    assert len(events) > 20, "a whole investigation streamed"
    banned = {"reasoning", "thought", "thinking", "chain_of_thought", "scratchpad", "prompt"}
    for event in events:
        assert not banned & set(event), event["type"]
    assert all(event["status_line"] for event in events)


async def test_the_sse_endpoint_replays_from_the_last_event_id(client, session) -> None:
    """A tab that dropped at 4 gets 5 onwards, and misses nothing.

    Driven through the route function rather than the test client: httpx's `ASGITransport`
    buffers a response body to completion before handing it back, and this stream is meant
    never to complete. Calling the endpoint keeps the part under test -- the cursor derived
    from `Last-Event-ID`, and the frames the bus actually emits -- and drops only the
    transport, which is not where the interesting behaviour is.
    """
    await client.post("/api/cycle/run")

    response = await events(session=session, last_event_id=4)

    assert response.media_type == "text/event-stream"
    assert response.headers["x-accel-buffering"] == "no"

    frames: list[str] = []
    stream = response.body_iterator
    while len(frames) < 3:
        frames.append(await asyncio.wait_for(anext(stream), timeout=5))
    await stream.aclose()

    payloads = [json.loads(f.split("data: ", 1)[1]) for f in frames]
    assert payloads[0]["type"] == "stream.opened"
    assert payloads[0]["replay_from"] == 5, "Last-Event-ID 4 resumes at 5, not at 4"
    assert [p["seq"] for p in payloads[1:]] == [5, 6]
    assert frames[1].startswith("id: 5\n"), "the frame carries the resume cursor"


async def test_the_stream_ignores_a_query_cursor_when_the_browser_states_its_own(
    client, session
) -> None:
    """The browser's resume cursor is a fact; a query parameter is only a preference."""
    await client.post("/api/cycle/run")

    response = await events(session=session, last_event_id=4, replay_from=0)
    opener = json.loads((await anext(response.body_iterator)).split("data: ", 1)[1])
    await response.body_iterator.aclose()

    assert opener["replay_from"] == 5


# --- approvals ------------------------------------------------------------------------------


@pytest.fixture
async def board(client, escalated):
    response = await client.get("/api/approvals")
    assert response.status_code == 200, response.text
    return response.json()


def _gated(board: dict) -> dict:
    return next(card for card in board["cards"] if card["state"] == "pending")


async def test_the_card_renders_exactly_the_eight_fields_in_order(board) -> None:
    card = _gated(board)

    assert list(card["card"]) == board["field_order"]
    assert all(value for value in card["card"].values())


async def test_auto_safe_rows_are_queued_and_never_get_a_card(board) -> None:
    queued = [row for row in board["worklist"] if row["status"] == "queued"]
    carded = {card["request"]["worklist_seq"] for card in board["cards"]}

    assert queued, "a collection call is reversible and is executed, not approved"
    assert all(row["seq"] not in carded for row in queued)


async def test_a_gated_row_names_who_must_sign_it(board) -> None:
    card = _gated(board)

    assert card["card"]["APPROVAL REQUIRED"] in {"treasurer", "cfo", "board"}
    assert card["verdict"]["risk"] == "requires_approval"
    assert card["verdict"]["basis"], "the classification shows its reasoning"


async def test_the_wrong_role_cannot_sign(client, board) -> None:
    card = next(c for c in board["cards"] if c["request"]["approval_required"] == "cfo")

    response = await client.post(
        f"/api/approvals/{card['request']['request_id']}/decide",
        json={"decided_by": TREASURER, "decided_by_role": "treasurer", "approved": True},
    )

    assert response.status_code == 403
    assert "routes it to cfo" in response.json()["detail"]


async def test_the_preparer_cannot_sign_their_own_card(client, board) -> None:
    card = _gated(board)

    response = await client.post(
        f"/api/approvals/{card['request']['request_id']}/decide",
        json={
            "decided_by": ANALYST,
            "decided_by_role": card["request"]["approval_required"],
            "approved": True,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"] == "segregation_of_duties"


async def test_a_rejection_must_state_a_reason(client, board) -> None:
    card = _gated(board)

    response = await client.post(
        f"/api/approvals/{card['request']['request_id']}/decide",
        json={
            "decided_by": CFO,
            "decided_by_role": card["request"]["approval_required"],
            "approved": False,
            "reason": "",
        },
    )

    assert response.status_code == 422


async def test_signing_records_the_card_that_was_seen(client, board) -> None:
    """A later edit upstream must not be able to rewrite what the approver signed."""
    card = _gated(board)
    request_id = card["request"]["request_id"]

    response = await client.post(
        f"/api/approvals/{request_id}/decide",
        json={
            "decided_by": CFO,
            "decided_by_role": card["request"]["approval_required"],
            "approved": True,
        },
    )

    assert response.status_code == 200
    entry = response.json()
    assert entry["card_seen"] == card["card"]
    assert entry["data_snapshot_ref"] == "fv-2026-W10"
    audit = (await client.get("/api/approvals/audit")).json()
    assert [e["request_id"] for e in audit] == [request_id]


async def test_a_decision_cannot_be_overwritten(client, board) -> None:
    card = _gated(board)
    request_id = card["request"]["request_id"]
    role = card["request"]["approval_required"]
    await client.post(
        f"/api/approvals/{request_id}/decide",
        json={"decided_by": CFO, "decided_by_role": role, "approved": True},
    )

    response = await client.post(
        f"/api/approvals/{request_id}/decide",
        json={
            "decided_by": CFO,
            "decided_by_role": role,
            "approved": False,
            "reason": "changed my mind",
        },
    )

    assert response.status_code == 403
    assert "already been decided" in response.json()["detail"]


# --- the execution gate ---------------------------------------------------------------------


async def test_a_pending_row_does_not_execute(client, board) -> None:
    seq = _gated(board)["request"]["worklist_seq"]

    response = await client.post(f"/api/approvals/worklist/{seq}/execute")

    assert response.status_code == 403
    assert "pending, not approved" in response.json()["detail"]


async def test_an_approved_row_executes_dry_run(client, board) -> None:
    card = _gated(board)
    await client.post(
        f"/api/approvals/{card['request']['request_id']}/decide",
        json={
            "decided_by": CFO,
            "decided_by_role": card["request"]["approval_required"],
            "approved": True,
        },
    )

    response = await client.post(
        f"/api/approvals/worklist/{card['request']['worklist_seq']}/execute"
    )

    assert response.status_code == 200
    assert response.json()["dry_run"] is True
    assert "dry run" in response.json()["receipt"]


async def test_an_auto_safe_row_executes_without_an_approval(client, board) -> None:
    queued = next(row for row in board["worklist"] if row["status"] == "queued")

    response = await client.post(f"/api/approvals/worklist/{queued['seq']}/execute")

    assert response.status_code == 200


# --- evidence ---------------------------------------------------------------------------------


async def test_a_cited_reference_resolves_to_a_source_row(client) -> None:
    response = await client.get(
        "/api/evidence", params={"reference": "ap_ledger:BILL-8863#due_date"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["resolved"]
    assert body["source"] == "ap_ledger"
    assert "Globex" in body["excerpt"]


async def test_an_unresolvable_reference_answers_rather_than_404s(client) -> None:
    """A visible break in the chain is recoverable; a 404 looks like a bad URL."""
    response = await client.get("/api/evidence", params={"reference": "ar_ledger:INV-NOPE"})

    assert response.status_code == 200
    assert response.json()["resolved"] is False
    assert response.json()["excerpt"], "the gap explains itself"


async def test_every_number_on_a_card_cites_something_that_resolves(client, board) -> None:
    """The Phase 10 done-when, asserted rather than asserted-to."""
    for card in board["cards"]:
        for evidence in card["request"]["evidence"]:
            response = await client.get(
                "/api/evidence", params={"reference": evidence["reference"]}
            )
            body = response.json()
            # A row composed by deterministic code cites `forecast:` and has no ledger row
            # underneath it by construction; anything claiming a ledger source must resolve.
            if not evidence["reference"].startswith("forecast:"):
                assert body["resolved"], evidence["reference"]
