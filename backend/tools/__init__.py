"""Deterministic tool layer. Agents call these; they never compute.

Bodies are stubs until Phase 3. Signatures are frozen — Person 2 codes against
them; Person 1 fills the implementations. Change only by agreement.
"""

from backend.tools.signatures import (
    get_accuracy_stats,
    get_bank_reconciliation,
    get_cash_position,
    get_covenant_status,
    get_debt_capacity,
    get_deferral_candidates,
    get_dodo_metrics,
    get_forecast,
    get_policy,
    get_variance_bridge,
    rank_collection_opportunities,
    validate_constraints,
)

__all__ = [
    "get_accuracy_stats",
    "get_bank_reconciliation",
    "get_cash_position",
    "get_covenant_status",
    "get_debt_capacity",
    "get_deferral_candidates",
    "get_dodo_metrics",
    "get_forecast",
    "get_policy",
    "get_variance_bridge",
    "rank_collection_opportunities",
    "validate_constraints",
]
