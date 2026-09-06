"""Covenant testing on the real test date, against the real definition.

`DATA_SOURCES.md` §3 states the trap plainly: real credit agreements define
Consolidated Leverage on *trailing-twelve-month adjusted* EBITDA and test it
**quarterly**, on a stated date. A system that evaluates the ratio continuously,
on unadjusted EBITDA, looks wrong to any CFO within about ten seconds — it will
report a "breach" in the middle of a quarter that the agreement does not test and
the lender will never see.

So a covenant here is a definition plus a test date. `evaluate` reports the value
it would take *if tested on its next test date*, together with the days remaining
and headroom stated two ways: in basis points against the ratio, and in currency
— because "we are 0.3x from the leverage covenant" is not actionable and
"we can carry $4.1M more net debt" is.

Ratios are basis points throughout. A covenant argued over in a lender call is
not a place for a float.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from backend.contracts.common import MoneyDTO
from backend.contracts.covenant import CovenantStatus
from backend.finance.errors import PolicyError
from backend.finance.money import Money

BPS = 10_000


class CovenantKind(StrEnum):
    """Direction of the test, which is what decides pass/fail and headroom."""

    #: Net Debt / TTM adjusted EBITDA — a ceiling.
    NET_DEBT_TO_EBITDA = "net_debt_to_ebitda"
    #: TTM adjusted EBITDA / TTM interest expense — a floor.
    INTEREST_COVERAGE = "interest_coverage"
    #: Unrestricted cash plus undrawn revolver — a currency floor.
    MIN_LIQUIDITY = "min_liquidity"
    #: Unrestricted cash — a currency floor.
    MIN_CASH = "min_cash"


class CovenantTestFrequency(StrEnum):
    """Named to avoid a leading `Test`, which pytest would try to collect."""

    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMIANNUAL = "semiannual"
    ANNUAL = "annual"


@dataclass(frozen=True, slots=True)
class CovenantDefinition:
    """A covenant as the credit agreement writes it.

    `definition` is the agreement's own language, carried through to the UI
    verbatim. It is the difference between "leverage covenant" and "Consolidated
    Total Net Debt to Consolidated Adjusted EBITDA for the trailing four fiscal
    quarters, tested on the last day of each fiscal quarter" — and only the
    second one lets a treasurer check whether we computed it correctly.
    """

    name: str
    kind: CovenantKind
    definition: str
    next_test_date: date
    threshold_bps: int | None = None
    threshold_amount: Money | None = None
    test_frequency: CovenantTestFrequency = CovenantTestFrequency.QUARTERLY
    cure_days: int = 0

    def __post_init__(self) -> None:
        ratio_kind = self.kind in (
            CovenantKind.NET_DEBT_TO_EBITDA,
            CovenantKind.INTEREST_COVERAGE,
        )
        if ratio_kind and self.threshold_bps is None:
            raise PolicyError(f"{self.name}: a ratio covenant needs threshold_bps")
        if not ratio_kind and self.threshold_amount is None:
            raise PolicyError(f"{self.name}: an amount covenant needs threshold_amount")


@dataclass(frozen=True, slots=True)
class CovenantInputs:
    """The measured quantities a covenant is tested against.

    Every one of these is an engine output. EBITDA is *adjusted* and *trailing
    twelve months* because that is what the agreement says; supplying a quarterly
    figure here silently multiplies leverage by four.
    """

    unrestricted_cash: Money
    total_debt: Money
    undrawn_revolver: Money
    ttm_adjusted_ebitda: Money
    ttm_interest_expense: Money

    @property
    def currency(self) -> str:
        return self.unrestricted_cash.currency.code

    @property
    def net_debt(self) -> Money:
        return self.total_debt - self.unrestricted_cash

    @property
    def liquidity(self) -> Money:
        return self.unrestricted_cash + self.undrawn_revolver


def _undefined(
    definition: CovenantDefinition, as_of: date, currency: str, reason: str
) -> CovenantStatus:
    """A covenant whose ratio has no arithmetic meaning this period.

    Zero or negative EBITDA is not a pass and it is not a divide-by-zero crash —
    it is a covenant that cannot be computed, which is itself the finding. The
    status fails so it surfaces, and `definition` carries the reason.
    """
    return CovenantStatus(
        name=definition.name,
        definition=f"{definition.definition} — not computable: {reason}",
        test_date=definition.next_test_date,
        current_value_bps=None,
        threshold_bps=definition.threshold_bps,
        headroom=None,
        headroom_bps=None,
        passed=False,
        days_to_test=(definition.next_test_date - as_of).days,
        currency=currency,
    )


def _ratio_status(
    definition: CovenantDefinition,
    as_of: date,
    *,
    value_bps: int,
    threshold_bps: int,
    is_ceiling: bool,
    headroom: Money,
) -> CovenantStatus:
    headroom_bps = threshold_bps - value_bps if is_ceiling else value_bps - threshold_bps
    return CovenantStatus(
        name=definition.name,
        definition=definition.definition,
        test_date=definition.next_test_date,
        current_value_bps=value_bps,
        threshold_bps=threshold_bps,
        headroom=MoneyDTO.from_money(headroom),
        headroom_bps=headroom_bps,
        passed=headroom_bps >= 0,
        days_to_test=(definition.next_test_date - as_of).days,
        currency=headroom.currency.code,
    )


def _amount_status(
    definition: CovenantDefinition,
    as_of: date,
    *,
    actual: Money,
    threshold: Money,
) -> CovenantStatus:
    headroom = actual - threshold
    return CovenantStatus(
        name=definition.name,
        definition=definition.definition,
        test_date=definition.next_test_date,
        current_value_bps=None,
        threshold_bps=None,
        headroom=MoneyDTO.from_money(headroom),
        headroom_bps=None,
        passed=headroom.amount >= 0,
        days_to_test=(definition.next_test_date - as_of).days,
        currency=headroom.currency.code,
    )


def evaluate(
    definition: CovenantDefinition,
    inputs: CovenantInputs,
    as_of: date,
) -> CovenantStatus:
    """Evaluate one covenant as it would be tested on its next test date."""
    currency = inputs.currency
    if definition.kind is CovenantKind.NET_DEBT_TO_EBITDA:
        threshold_bps = _require_bps(definition)
        ebitda = inputs.ttm_adjusted_ebitda
        if ebitda.amount <= 0:
            return _undefined(definition, as_of, currency, "TTM adjusted EBITDA is not positive")
        net_debt = inputs.net_debt
        value_bps = net_debt.amount * BPS // ebitda.amount
        # Headroom in currency: how much more net debt fits under the ceiling.
        max_net_debt = Money.of(threshold_bps * ebitda.amount // BPS, ebitda.currency)
        return _ratio_status(
            definition,
            as_of,
            value_bps=value_bps,
            threshold_bps=threshold_bps,
            is_ceiling=True,
            headroom=max_net_debt - net_debt,
        )

    if definition.kind is CovenantKind.INTEREST_COVERAGE:
        threshold_bps = _require_bps(definition)
        interest = inputs.ttm_interest_expense
        if interest.amount <= 0:
            return _undefined(definition, as_of, currency, "TTM interest expense is not positive")
        ebitda = inputs.ttm_adjusted_ebitda
        value_bps = ebitda.amount * BPS // interest.amount
        # Headroom in currency: EBITDA above the minimum the ratio demands.
        required_ebitda = Money.of(threshold_bps * interest.amount // BPS, interest.currency)
        return _ratio_status(
            definition,
            as_of,
            value_bps=value_bps,
            threshold_bps=threshold_bps,
            is_ceiling=False,
            headroom=ebitda - required_ebitda,
        )

    threshold = _require_amount(definition)
    actual = (
        inputs.liquidity
        if definition.kind is CovenantKind.MIN_LIQUIDITY
        else inputs.unrestricted_cash
    )
    if actual.currency.code != threshold.currency.code:
        raise PolicyError(
            f"{definition.name}: threshold currency {threshold.currency.code} "
            f"!= measured currency {actual.currency.code}"
        )
    return _amount_status(definition, as_of, actual=actual, threshold=threshold)


def _require_bps(definition: CovenantDefinition) -> int:
    if definition.threshold_bps is None:  # pragma: no cover - guarded in __post_init__
        raise PolicyError(f"{definition.name}: missing threshold_bps")
    return definition.threshold_bps


def _require_amount(definition: CovenantDefinition) -> Money:
    if definition.threshold_amount is None:  # pragma: no cover - guarded in __post_init__
        raise PolicyError(f"{definition.name}: missing threshold_amount")
    return definition.threshold_amount


def evaluate_all(
    definitions: tuple[CovenantDefinition, ...],
    inputs: CovenantInputs,
    as_of: date,
) -> tuple[CovenantStatus, ...]:
    """Evaluate every covenant, nearest test date first."""
    statuses = tuple(evaluate(definition, inputs, as_of) for definition in definitions)
    return tuple(sorted(statuses, key=lambda status: (status.test_date, status.name)))


def tested_within(
    definitions: tuple[CovenantDefinition, ...],
    as_of: date,
    horizon_end: date,
) -> tuple[CovenantDefinition, ...]:
    """Covenants whose test date falls inside the forecast horizon.

    A covenant tested after the horizon cannot be breached by anything in the
    horizon, so the constraint gate treats it as absent rather than as passing —
    the distinction matters when a treasurer asks why no covenant was checked.
    """
    return tuple(
        definition
        for definition in definitions
        if as_of <= definition.next_test_date <= horizon_end
    )


def binding_headroom(statuses: tuple[CovenantStatus, ...]) -> CovenantStatus | None:
    """The covenant with the least currency headroom — the one that binds."""
    with_headroom = [status for status in statuses if status.headroom is not None]
    if not with_headroom:
        return None
    return min(with_headroom, key=lambda status: (status.headroom.amount, status.name))  # type: ignore[union-attr]


__all__ = [
    "BPS",
    "CovenantDefinition",
    "CovenantInputs",
    "CovenantKind",
    "CovenantTestFrequency",
    "binding_headroom",
    "evaluate",
    "evaluate_all",
    "tested_within",
]
