"""The two new HTTP surfaces: the DB Agent's stages, and starting a single agent.

Both routers are thin, so what these tests really check is the boundary: that a stage
gate returns a status the UI can branch on, that a credential never comes back out, and
that an agent started by hand goes through exactly the runtime the Commander uses.

`ASGITransport` follows the convention in `tests/e2e/conftest.py` — real routing,
validation, dependency resolution and exception handlers, no socket.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api import onboarding as onboarding_routes
from backend.api import store
from backend.api.session import Session, reset_session
from backend.main import create_app
from scripts import demo_company

ENTITIES = {
    "BankAccount",
    "BankTransaction",
    "Customer",
    "Invoice",
    "Vendor",
    "VendorInvoice",
}


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    """A fresh app, a fresh session, and a target database of this test's own."""
    monkeypatch.setenv("WARROOM_TENANT_DB", f"sqlite:///{tmp_path / 'tenant.db'}")
    store.reset()
    reset_session(Session())
    monkeypatch.setattr(onboarding_routes, "_current", None, raising=False)
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://warroom.test") as opened:
        yield opened
    # A successful load binds a `TenantToolset`, and that toolset holds a read connection
    # to the target database for its lifetime. Replacing the session releases it; disposing
    # the engine underneath a still-checked-out connection is what leaks it.
    reset_session(Session())
    store.reset()


@pytest.fixture
def helios(tmp_path: Path) -> dict[str, object]:
    return demo_company.build_all(tmp_path / "sources", keys=["helios"])[0]


async def _walk_to_classified(client: AsyncClient, source: dict[str, object]) -> None:
    connected = await client.post(
        "/api/onboarding/connect",
        json={
            "url": str(source["url"]),
            "company": str(source["company"]),
            "tenant_id": str(source["key"]),
            "currency": str(source["currency"]),
        },
    )
    assert connected.status_code == 200, connected.text
    assert (await client.post("/api/onboarding/introspect")).status_code == 200
    assert (await client.post("/api/onboarding/classify")).status_code == 200


def _controls(source: dict[str, object]) -> dict[str, object]:
    balances = source["control_balances"]
    assert isinstance(balances, dict)
    return {
        "ar_minor": balances["ar_control_minor"],
        "ap_minor": balances["ap_control_minor"],
        "cash_minor": balances["cash_control_minor"],
        "currency": balances["currency"],
    }


class TestOnboardingRoutes:
    async def test_a_stage_out_of_order_is_refused_with_the_missing_step_named(
        self, client: AsyncClient
    ) -> None:
        response = await client.post("/api/onboarding/introspect")
        assert response.status_code == 409
        assert "connect" in response.json()["detail"]

    async def test_an_unreachable_source_is_a_bad_request_not_a_crash(
        self, client: AsyncClient
    ) -> None:
        response = await client.post(
            "/api/onboarding/connect",
            json={
                "url": "postgresql+psycopg://u:p@127.0.0.1:1/none",
                "company": "Nowhere Ltd",
                "tenant_id": "nowhere",
            },
        )
        assert response.status_code == 400

    async def test_a_failed_connection_does_not_leak_the_password(
        self, client: AsyncClient
    ) -> None:
        """The likeliest leak is not the success path -- it is the error message.

        A driver's exception text contains the DSN it was handed, so an unreachable
        source is exactly the case where a raw string would end up in a response body,
        a log line and a browser's network tab at once.
        """
        password = "s3cret-treasury-password"
        response = await client.post(
            "/api/onboarding/connect",
            json={
                "url": f"postgresql+psycopg://treasury:{password}@127.0.0.1:1/erp",
                "company": "Nowhere Ltd",
                "tenant_id": "nowhere",
            },
        )
        assert response.status_code == 400
        assert password not in response.text
        assert password not in (await client.get("/api/onboarding")).text

    async def test_the_whole_pipeline_runs_from_a_pasted_url(
        self, client: AsyncClient, helios: dict[str, object]
    ) -> None:
        connected = await client.post(
            "/api/onboarding/connect",
            json={
                "url": str(helios["url"]),
                "company": str(helios["company"]),
                "tenant_id": "helios",
                "currency": "USD",
            },
        )
        assert connected.status_code == 200
        assert connected.json()["probe"]["can_write"] is True

        introspected = (await client.post("/api/onboarding/introspect")).json()
        assert len(introspected["tables"]) == 7

        classified = (await client.post("/api/onboarding/classify")).json()
        assert {row["entity_role"] for row in classified["tables"]} == ENTITIES
        assert classified["model_calls"] == 0

        loaded = (await client.post("/api/onboarding/load", json=_controls(helios))).json()
        assert loaded["accepted"] is True
        assert loaded["committed"] is True
        assert loaded["mapping_path"]

        ledger = (await client.get("/api/onboarding/ledger")).json()
        assert ledger["counts"]["Invoice"] == loaded["counts"]["Invoice"]
        assert ledger["totals"]["open_ar_minor"] == _controls(helios)["ar_minor"]

    async def test_a_failed_reconciliation_commits_nothing(
        self, client: AsyncClient, helios: dict[str, object]
    ) -> None:
        await _walk_to_classified(client, helios)
        loaded = (
            await client.post(
                "/api/onboarding/load",
                json={"ar_minor": 1, "ap_minor": 2, "cash_minor": 3, "currency": "USD"},
            )
        ).json()

        assert loaded["accepted"] is False
        assert loaded["reconciliation"]["failures"]
        ledger = (await client.get("/api/onboarding/ledger")).json()
        assert ledger["counts"]["Invoice"] == 0

    async def test_drift_reports_no_change_against_the_schema_it_froze(
        self, client: AsyncClient, helios: dict[str, object]
    ) -> None:
        await _walk_to_classified(client, helios)
        await client.post("/api/onboarding/load", json=_controls(helios))
        assert (await client.post("/api/onboarding/drift")).json()["changed"] is False

    async def test_the_catalog_lists_demo_tenants_or_says_why_it_cannot(
        self, client: AsyncClient
    ) -> None:
        body = (await client.get("/api/onboarding/companies")).json()
        assert "companies" in body
        if not body["available"]:
            assert body["hint"], "an empty catalog must explain itself"

    async def test_reset_drops_the_connection_and_the_credential(
        self, client: AsyncClient, helios: dict[str, object]
    ) -> None:
        await _walk_to_classified(client, helios)
        assert (await client.post("/api/onboarding/reset")).json()["connected"] is False
        assert (await client.post("/api/onboarding/introspect")).status_code == 409


class TestAgentRoutes:
    async def test_the_roster_describes_every_startable_specialist(
        self, client: AsyncClient
    ) -> None:
        roster = (await client.get("/api/agents")).json()
        assert {card["role"] for card in roster["agents"]} == {
            "forecast",
            "variance",
            "ar_collections",
            "ap_optimization",
            "supplier_risk",
            "dodo_revenue",
        }
        for card in roster["agents"]:
            assert card["tools"], f"{card['role']} has no tool allowlist"
            assert card["model"] and card["purpose"] and card["asks"]

    async def test_starting_an_agent_produces_a_cited_finding(self, client: AsyncClient) -> None:
        run = (await client.post("/api/agents/ar_collections/run", json={})).json()
        assert run["agent"] == "ar_collections"
        assert run["status"] == "complete"
        assert run["finding"]["evidence"], "a complete finding must carry evidence"

    async def test_a_hand_started_run_reaches_the_war_room_stream(
        self, client: AsyncClient
    ) -> None:
        """Same bus, same run store. The trigger differs; nothing else does."""
        await client.post("/api/agents/forecast/run", json={})
        history = (await client.get("/api/war-room/history")).json()
        assert any(event.get("agent") == "forecast" for event in history["events"])

    async def test_a_coordinating_role_cannot_be_started_as_a_specialist(
        self, client: AsyncClient
    ) -> None:
        response = await client.post("/api/agents/commander/run", json={})
        assert response.status_code == 409
        assert "Commander" in response.json()["detail"]

    async def test_an_unknown_role_is_rejected_by_the_enum_not_the_handler(
        self, client: AsyncClient
    ) -> None:
        assert (await client.post("/api/agents/not_an_agent/run", json={})).status_code == 422

    async def test_the_roster_carries_forward_what_each_agent_last_said(
        self, client: AsyncClient
    ) -> None:
        await client.post("/api/agents/dodo_revenue/run", json={})
        roster = (await client.get("/api/agents")).json()
        card = next(row for row in roster["agents"] if row["role"] == "dodo_revenue")
        assert card["last_run"] is not None
        assert card["last_run"]["finding"]["headline"]

    async def test_hand_started_runs_are_listed_separately_from_the_wave(
        self, client: AsyncClient
    ) -> None:
        await client.post("/api/agents/variance/run", json={})
        roster = (await client.get("/api/agents")).json()
        assert [run["agent"] for run in roster["manual_runs"]] == ["variance"]


class TestDataSourceLifecycle:
    """The rule the whole product hangs off: a screen shows one source, or nothing."""

    async def test_nothing_is_loaded_until_something_is_loaded(self, client: AsyncClient) -> None:
        status = (await client.post("/api/data-source/clear")).json()
        assert status["source"] is None
        assert (await client.get("/api/data-source/position")).status_code == 409

    async def test_an_agent_cannot_be_started_without_a_source(self, client: AsyncClient) -> None:
        """A finding about nothing is worse than no finding: this is a 409, not an empty run."""
        await client.post("/api/data-source/clear")
        response = await client.post("/api/agents/ar_collections/run", json={})
        assert response.status_code == 409
        assert "data source" in response.json()["detail"]

    async def test_a_committed_load_becomes_the_source_for_every_screen(
        self, client: AsyncClient, helios: dict[str, object]
    ) -> None:
        await _walk_to_classified(client, helios)
        loaded = (await client.post("/api/onboarding/load", json=_controls(helios))).json()
        assert loaded["accepted"] is True
        assert loaded["data_source"]["tenant_id"] == "helios"

        status = (await client.get("/api/data-source")).json()
        assert status["source"]["kind"] == "tenant"
        assert status["source"]["company"] == helios["company"]
        assert status["source"]["counts"]["Invoice"] > 0
        # The as-of is the ledger's own most recent dated row, not today.
        assert status["source"]["as_of"].startswith("2026-")

        position = (await client.get("/api/data-source/position")).json()
        assert len(position["forecast"]["weeks"]) == 13
        assert position["liquidity"]["cash_today"]["minor_units"] == _controls(helios)["cash_minor"]
        # What this tenant does not have is named, with the reason, rather than omitted.
        assert "dodo" in position["capabilities"]["missing_sources"]
        assert position["capabilities"]["notes"]["dodo"]

    async def test_a_failed_reconciliation_binds_nothing(
        self, client: AsyncClient, helios: dict[str, object]
    ) -> None:
        await _walk_to_classified(client, helios)
        loaded = (
            await client.post(
                "/api/onboarding/load",
                json={"ar_minor": 1, "ap_minor": 2, "cash_minor": 3, "currency": "USD"},
            )
        ).json()
        assert loaded["accepted"] is False
        assert loaded["data_source"] is None
        assert (await client.get("/api/data-source")).json()["source"] is None

    async def test_connecting_a_new_source_clears_what_the_last_one_produced(
        self, client: AsyncClient, helios: dict[str, object]
    ) -> None:
        """The requirement in one test: onboarding again empties every screen."""
        await _walk_to_classified(client, helios)
        await client.post("/api/onboarding/load", json=_controls(helios))
        await client.post("/api/agents/ar_collections/run", json={})
        assert (await client.get("/api/data-source")).json()["agent_runs"] > 0

        await client.post(
            "/api/onboarding/connect",
            json={
                "url": str(helios["url"]),
                "company": str(helios["company"]),
                "tenant_id": "helios",
                "currency": "USD",
            },
        )
        after = (await client.get("/api/data-source")).json()
        assert after["source"] is None
        assert after["agent_runs"] == 0
        assert after["has_cycle"] is False

    async def test_resetting_the_onboarding_unloads_the_product(
        self, client: AsyncClient, helios: dict[str, object]
    ) -> None:
        await _walk_to_classified(client, helios)
        await client.post("/api/onboarding/load", json=_controls(helios))
        await client.post("/api/onboarding/reset")
        assert (await client.get("/api/data-source")).json()["source"] is None

    async def test_the_recorded_demo_is_bindable_and_labelled_as_synthetic(
        self, client: AsyncClient
    ) -> None:
        status = (await client.post("/api/data-source/demo")).json()
        assert status["source"]["kind"] == "demo"
        assert "Synthetic" in status["source"]["note"] or "synthetic" in status["source"]["note"]
        assert (await client.get("/api/data-source/position")).status_code == 200

    async def test_the_policy_floor_is_per_source_and_re_evaluates(
        self, client: AsyncClient, helios: dict[str, object]
    ) -> None:
        """NovaTech's $15M floor is not every tenant's floor, so it is a setting."""
        await _walk_to_classified(client, helios)
        await client.post("/api/onboarding/load", json=_controls(helios))
        before = (await client.get("/api/data-source/position")).json()
        raised = before["liquidity"]["min_cash"]["minor_units"] + 1_000_00

        after = (
            await client.post(
                "/api/data-source/policy",
                json={
                    "min_unrestricted_cash_minor": raised,
                    "min_30d_liquidity_minor": raised,
                },
            )
        ).json()
        assert after["liquidity"]["floor"]["minor_units"] == raised
        assert any(week["breaches_floor"] for week in after["forecast"]["weeks"])

    async def test_the_demo_ledger_will_not_take_a_tenant_policy(self, client: AsyncClient) -> None:
        await client.post("/api/data-source/demo")
        response = await client.post(
            "/api/data-source/policy",
            json={"min_unrestricted_cash_minor": 1, "min_30d_liquidity_minor": 1},
        )
        assert response.status_code == 409


class TestAgentDependencies:
    """Dependencies are stated on the card, and they are real, not decorative."""

    async def test_supplier_risk_declares_its_upstream_and_reports_it_unmet(
        self, client: AsyncClient
    ) -> None:
        await client.post("/api/data-source/demo")
        roster = (await client.get("/api/agents")).json()
        card = next(row for row in roster["agents"] if row["role"] == "supplier_risk")
        assert [item["role"] for item in card["depends_on"]] == ["ap_optimization"]
        assert card["depends_on"][0]["satisfied"] is False
        assert card["ready"] is False
        assert "AP" in (card["blocked_reason"] or "")
        # Blocked is a warning, never a lock: an agent that can only answer half the
        # question should answer half of it and say which half is missing.
        assert card["startable"] is True

    async def test_running_the_upstream_agent_satisfies_the_edge(self, client: AsyncClient) -> None:
        await client.post("/api/data-source/demo")
        await client.post("/api/agents/ap_optimization/run", json={})
        roster = (await client.get("/api/agents")).json()
        card = next(row for row in roster["agents"] if row["role"] == "supplier_risk")
        assert card["depends_on"][0]["satisfied"] is True
        assert card["ready"] is True

    async def test_a_missing_source_is_named_on_the_card_that_needs_it(
        self, client: AsyncClient, helios: dict[str, object]
    ) -> None:
        await _walk_to_classified(client, helios)
        await client.post("/api/onboarding/load", json=_controls(helios))
        roster = (await client.get("/api/agents")).json()
        card = next(row for row in roster["agents"] if row["role"] == "dodo_revenue")
        assert card["missing_sources"] == ["dodo"]
        assert "dodo" in (card["blocked_reason"] or "")

    async def test_the_graph_edges_are_declared_once_each(self, client: AsyncClient) -> None:
        await client.post("/api/data-source/demo")
        edges = (await client.get("/api/agents")).json()["edges"]
        pairs = [(edge["from"], edge["to"]) for edge in edges]
        assert len(pairs) == len(set(pairs))
        assert ("ap_optimization", "supplier_risk") in pairs
