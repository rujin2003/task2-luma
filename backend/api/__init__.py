"""The HTTP surface. Person 2's half of the boundary with Person 1's engine.

Assembly only: every router here is thin, and the rule it follows is the same one the
tool layer follows -- the API does not compute anything. It sequences calls into the
orchestrator, renders what comes back, and turns a domain refusal into a status code the
UI can branch on. A number that appears in a response was computed by Person 1's engine
and travelled here through a typed contract.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.api import (
    agents,
    approvals,
    cycle,
    datasource,
    errors,
    evidence,
    onboarding,
    warroom,
)

router = APIRouter()
router.include_router(cycle.router)
router.include_router(warroom.router)
router.include_router(approvals.router)
router.include_router(evidence.router)
# Onboarding and the agent space sit outside the Monday sequence: one runs before a
# tenant has a cycle at all, the other runs whenever an analyst wants a second opinion.
router.include_router(onboarding.router)
router.include_router(agents.router)
# The data source is what every other screen is a view of, so it sits above all of them.
router.include_router(datasource.router)

__all__ = ["errors", "router"]
