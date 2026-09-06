"""The tool surface as plain function signatures -- the readable index of `Toolset`.

Bodies raise `NotImplementedError` by design: this module documents and type-checks the
contract, while `EngineToolset` and `FixtureToolset` provide the implementations.
"""

from __future__ import annotations

from datetime import date

from backend.contracts.cash import BankRecon, CashPosition
from backend.contracts.constraint import Constraint
from backend.contracts.covenant import CovenantStatus
from backend.contracts.debt import DebtCapacity
from backend.contracts.dodo import DodoMetrics
from backend.contracts.forecast import AccuracyStatDTO, ForecastGrid, VarianceBridge
from backend.contracts.worklist import CollectionOpportunity, DeferralCandidate
from backend.finance.policy import TreasuryPolicy


def get_policy(*, tenant_id: str) -> TreasuryPolicy:
    """Return the effective TreasuryPolicy for the tenant."""
    raise NotImplementedError("get_policy")


def get_cash_position(*, tenant_id: str, as_of: date | None = None) -> CashPosition:
    """Current cash truth: restricted, unrestricted, pending, available liquidity."""
    raise NotImplementedError("get_cash_position")


def get_forecast(*, tenant_id: str, version_id: str | None = None) -> ForecastGrid:
    """13-week rolling direct-method forecast. Engine computes; agents only read."""
    raise NotImplementedError("get_forecast")


def get_variance_bridge(
    *,
    tenant_id: str,
    kind: str,
    week_ending: date | None = None,
) -> VarianceBridge:
    """Forecast-vs-actual or forecast-vs-prior, materiality-gated."""
    raise NotImplementedError("get_variance_bridge")


def get_accuracy_stats(
    *,
    tenant_id: str,
    window_weeks: int = 26,
) -> tuple[AccuracyStatDTO, ...]:
    """MAPE by category x horizon over a trailing window."""
    raise NotImplementedError("get_accuracy_stats")


def get_covenant_status(*, tenant_id: str, as_of: date | None = None) -> tuple[CovenantStatus, ...]:
    """Covenants on their real test date and definition, not a live ratio."""
    raise NotImplementedError("get_covenant_status")


def get_debt_capacity(*, tenant_id: str) -> tuple[DebtCapacity, ...]:
    """Facility limit, drawn, undrawn, utilization, all-in draw cost, max safe draw."""
    raise NotImplementedError("get_debt_capacity")


def get_bank_reconciliation(
    *,
    tenant_id: str,
    account_ref: str,
    as_of: date,
) -> BankRecon:
    """Book/bank reconciling artifact with aged exceptions and sign-off block."""
    raise NotImplementedError("get_bank_reconciliation")


def rank_collection_opportunities(
    *,
    tenant_id: str,
    top_n: int = 10,
) -> tuple[CollectionOpportunity, ...]:
    """Pre-ranked AR opportunities. Arithmetic is done here; the model does not sum."""
    raise NotImplementedError("rank_collection_opportunities")


def get_deferral_candidates(
    *,
    tenant_id: str,
    top_n: int = 10,
) -> tuple[DeferralCandidate, ...]:
    """AP deferral candidates. Protected classes are blocked upstream by policy."""
    raise NotImplementedError("get_deferral_candidates")


def get_dodo_metrics(*, tenant_id: str) -> DodoMetrics:
    """Expected / at-risk / recoverable collections. Degrades if Dodo is down."""
    raise NotImplementedError("get_dodo_metrics")


def validate_constraints(
    *,
    tenant_id: str,
    action: str,
    amount_minor: int,
    currency: str,
    payment_class: str | None = None,
) -> tuple[Constraint, ...]:
    """Hard-constraint gate. Payroll and tax delays fail closed via policy."""
    raise NotImplementedError("validate_constraints")
