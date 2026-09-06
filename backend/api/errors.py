"""Domain failures, mapped to status codes without being flattened into strings.

The rule here is that a refusal is a *result*, not an accident. When the ledger refuses an
approval because the preparer tried to sign their own card, the UI needs to render that
sentence to the analyst -- it is the control working, and hiding it behind a generic 500
would turn the most interesting thing the system does into a stack trace in a log nobody
reads. So each domain exception gets a status the UI can branch on and a body that carries
the reason verbatim.

The codes are chosen for what the client should *do*:

* `409` -- the request was well-formed but the session is not at that step yet. Retry
  after doing the step it names.
* `403` -- a control refused. Do not retry; a different person has to act, or nobody can.
* `422` -- the payload does not validate. FastAPI's own handler covers this.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from backend.api.session import SequenceError
from backend.contracts.approvals import SegregationOfDutiesError
from backend.finance.controls import ApprovalRequiredError
from backend.orchestrator.review import ClosedPeriodError
from backend.tools.toolset import ToolError


def _body(kind: str, detail: str) -> dict[str, str]:
    """`kind` is for the UI to branch on; `detail` is for the human to read."""
    return {"error": kind, "detail": detail}


def install(app: FastAPI) -> None:
    """Register every domain handler. Called once, from `create_app`."""

    @app.exception_handler(SequenceError)
    async def _out_of_order(_: Request, exc: SequenceError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_body("out_of_order", str(exc)),
        )

    @app.exception_handler(SegregationOfDutiesError)
    async def _segregation(_: Request, exc: SegregationOfDutiesError) -> JSONResponse:
        # Not a 401: the caller is who they say they are, and that is the problem.
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=_body("segregation_of_duties", str(exc)),
        )

    @app.exception_handler(ApprovalRequiredError)
    async def _approval_required(_: Request, exc: ApprovalRequiredError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=_body("approval_required", str(exc)),
        )

    @app.exception_handler(ClosedPeriodError)
    async def _closed_period(_: Request, exc: ClosedPeriodError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=_body("closed_period", str(exc)),
        )

    @app.exception_handler(ValidationError)
    async def _contract_rejected(_: Request, exc: ValidationError) -> JSONResponse:
        """A contract refusing a payload the request schema could not have caught.

        `Override` will not accept a change that changes nothing, and that rule lives on
        the contract rather than on the request model -- deliberately, so it holds however
        the object is built. Constructing one inside a handler therefore raises here, and
        it is the same class of problem as a bad body: 422, with the field that objected.
        """
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=jsonable_encoder({"error": "invalid", "detail": exc.errors(include_url=False)}),
        )

    @app.exception_handler(ToolError)
    async def _tool_failed(_: Request, exc: ToolError) -> JSONResponse:
        # A tool that cannot answer is a degraded system, not a bad request. The UI
        # renders degradation as a first-class state, so it needs to be able to tell.
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_body("degraded", str(exc)),
        )
