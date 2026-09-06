"""One app, one fresh session per test, driven over real HTTP semantics.

`ASGITransport` runs the app in-process without a socket, so these are fast enough to be
part of the normal suite while still going through routing, validation, dependency
resolution and the exception handlers -- which is where the interesting behaviour is. A
test that called `Session` directly would not notice that a segregation-of-duties refusal
had stopped being a 403.

The session is reset per test rather than shared: the whole thing is a state machine with
gates in it, and a test that inherited a published version from the test before it would
be asserting against a Monday that had already happened.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.session import Session, reset_session
from backend.main import create_app

TREASURER = "treasurer@novatech"
CFO = "cfo@novatech"
ANALYST = "analyst@novatech"  # the preparer; may not sign their own work


@pytest.fixture
def session() -> Session:
    return reset_session()


@pytest.fixture
async def client(session: Session):
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://warroom.test") as client:
        yield client


@pytest.fixture
async def published(client):
    """A cycle run and published: the state the policy check is allowed to run against."""
    await client.post("/api/cycle/run")
    response = await client.post(
        "/api/cycle/publish",
        json={"published_by": TREASURER, "published_by_role": "treasurer"},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
async def escalated(client, published):
    """Step 10 found a breach and the war room ran. The golden path, up to the cards."""
    check = await client.post("/api/cycle/policy-check")
    assert check.status_code == 200, check.text
    assert check.json()["escalate"], "the seeded Monday is meant to breach the floor"
    opened = await client.post("/api/war-room/open")
    assert opened.status_code == 200, opened.text
    return opened.json()
