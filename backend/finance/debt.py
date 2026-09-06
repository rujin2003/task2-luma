"""Facility capacity, draw cost and amortisation.

Absorbed from the cut Debt Agent (`PHASES.md`): "compute available capacity" is
arithmetic against a facility table, and arithmetic belongs in the engine where
it can be unit-tested, not in a model that might round it.

Two details separate this from a subtraction:

* **Max *safe* draw is not undrawn capacity.** It is bounded by the tighter of
  the facility's own utilization covenant and the tenant's `TreasuryPolicy`
  ceiling. A revolver with $10M undrawn and a 70% utilization ceiling may have
  only $2M of drawable room, and a recommendation that ignores this proposes a
  draw that trips a covenant on the way to fixing a cash floor.
* **Interest is actual/360**, the money-market convention these facilities are
  written on, not `days / 365`. Computed in integer minor units with explicit
  rounding.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from backend.contracts.common import MoneyDTO
from backend.contracts.debt import DebtCapacity
from backend.finance.cadence import BusinessCalendar, Convention, ServiceFrequency, add_months
from backend.finance.errors import PolicyError
from backend.finance.money import Money
from backend.finance.policy import TreasuryPolicy
from backend.finance.rounding import Rounding, div_round

BPS = 10_000

#: Money-market day count. Revolvers and term loans of this size are actual/360.
DAY_COUNT_BASIS = 360


@dataclass(frozen=True, slots=True)
class Facility:
    """A credit facility as the engine reads it."""

    facility_id: str
    name: str
    limit: Money
    drawn: Money
    base_rate_bps: int
    spread_bps: int
    maturity: date
    commitment_fee_bps: int = 0
    max_utilization_bps: int = BPS
    is_revolver: bool = True

    def __post_init__(self) -> None:
        if self.limit.currency.code != self.drawn.currency.code:
            raise PolicyError(f"{self.name}: limit and drawn currencies differ")
        if self.limit.amount < 0 or self.drawn.amount < 0:
            raise PolicyError(f"{self.name}: limit and drawn must be non-negative")
        if self.drawn > self.limit:
            raise PolicyError(f"{self.name}: drawn {self.drawn} exceeds limit {self.limit}")
        if not 0 <= self.max_utilization_bps <= BPS:
            raise PolicyError(f"{self.name}: max_utilization_bps must be within 0..10000")

    @property
    def currency(self) -> str:
        return self.limit.currency.code

    @property
    def undrawn(self) -> Money:
        return self.limit - self.drawn


def all_in_draw_cost_bps(facility: Facility) -> int:
    """Base rate plus credit spread — the rate a new draw actually costs.

    The commitment fee is excluded on purpose: it is paid on the *undrawn*
    balance whether or not we draw, so including it in the draw cost double-counts
    a sunk cost and makes drawing look more expensive than it is.
    """
    return facility.base_rate_bps + facility.spread_bps


def utilization_bps(facility: Facility) -> int:
    """Drawn as a proportion of the limit, in basis points."""
    if facility.limit.amount == 0:
        return 0
    return facility.drawn.amount * BPS // facility.limit.amount


def effective_utilization_ceiling(facility: Facility, policy: TreasuryPolicy) -> int:
    """The binding ceiling: the tighter of facility covenant and tenant policy."""
    return min(facility.max_utilization_bps, policy.max_revolver_utilization_bps)


def max_safe_draw(facility: Facility, policy: TreasuryPolicy) -> Money:
    """Largest draw that leaves utilization inside the binding ceiling.

    Never negative: a facility already over its ceiling has zero safe capacity,
    not negative capacity, and the overage is a covenant finding rather than a
    draw recommendation.
    """
    ceiling_bps = effective_utilization_ceiling(facility, policy)
    max_drawn = Money.of(facility.limit.amount * ceiling_bps // BPS, facility.limit.currency)
    headroom = max_drawn - facility.drawn
    if headroom.amount < 0:
        return Money.zero(facility.limit.currency)
    return headroom


def capacity(facility: Facility, policy: TreasuryPolicy) -> DebtCapacity:
    """The tool-layer view of one facility."""
    return DebtCapacity(
        facility_id=facility.facility_id,
        limit=MoneyDTO.from_money(facility.limit),
        drawn=MoneyDTO.from_money(facility.drawn),
        undrawn=MoneyDTO.from_money(facility.undrawn),
        utilization_bps=utilization_bps(facility),
        all_in_draw_cost_bps=all_in_draw_cost_bps(facility),
        max_safe_draw=MoneyDTO.from_money(max_safe_draw(facility, policy)),
        commitment_fee_bps=facility.commitment_fee_bps,
    )


def accrued_interest(
    principal: Money,
    rate_bps: int,
    days: int,
    *,
    rounding: Rounding = Rounding.HALF_EVEN,
) -> Money:
    """Actual/360 interest on `principal` for `days`, in minor units."""
    if days < 0:
        raise PolicyError("interest cannot accrue over a negative number of days")
    if rate_bps < 0:
        raise PolicyError("interest rate cannot be negative")
    numerator = principal.amount * rate_bps * days
    minor = div_round(numerator, BPS * DAY_COUNT_BASIS, rounding)
    return Money.of(minor, principal.currency)


def draw_cost(
    facility: Facility,
    amount: Money,
    days: int,
    *,
    rounding: Rounding = Rounding.HALF_EVEN,
) -> Money:
    """Interest cost of holding a draw of `amount` for `days`.

    This is the number the AP-deferral-versus-draw comparison turns on: a
    two-week revolver draw is often cheaper than a 2/10 net 30 discount forgone,
    and neither side of that comparison is guessable.
    """
    if amount.currency.code != facility.currency:
        raise PolicyError(f"{facility.name}: draw currency does not match the facility")
    return accrued_interest(amount, all_in_draw_cost_bps(facility), days, rounding=rounding)


def commitment_fee(
    facility: Facility,
    days: int,
    *,
    rounding: Rounding = Rounding.HALF_EVEN,
) -> Money:
    """Fee on the undrawn balance — payable whether or not the facility is used."""
    return accrued_interest(facility.undrawn, facility.commitment_fee_bps, days, rounding=rounding)


@dataclass(frozen=True, slots=True)
class AmortisationRow:
    """One scheduled debt-service date: principal, interest, closing balance."""

    due_date: date
    principal: Money
    interest: Money
    closing_balance: Money

    @property
    def total(self) -> Money:
        return self.principal + self.interest


def amortisation_schedule(
    principal: Money,
    *,
    first_payment: date,
    periods: int,
    rate_bps: int,
    calendar: BusinessCalendar,
    frequency: ServiceFrequency = ServiceFrequency.QUARTERLY,
) -> tuple[AmortisationRow, ...]:
    """Straight-line principal amortisation with interest on the closing balance.

    Principal is split with `Money.allocate`, so the instalments sum back to the
    original balance exactly and the final row closes at zero — a schedule that
    leaves a stray minor unit outstanding is the kind of defect that surfaces
    only at maturity.
    """
    if periods < 1:
        raise PolicyError("an amortisation schedule needs at least one period")
    if principal.amount < 0:
        raise PolicyError("amortising principal must be non-negative")

    instalments = principal.allocate([1] * periods)
    months = {
        ServiceFrequency.MONTHLY: 1,
        ServiceFrequency.QUARTERLY: 3,
        ServiceFrequency.SEMIANNUAL: 6,
        ServiceFrequency.ANNUAL: 12,
    }[frequency]

    rows: list[AmortisationRow] = []
    balance = principal
    previous = first_payment
    for index, instalment in enumerate(instalments):
        raw_due = add_months(first_payment, months * index)
        due = calendar.adjust(raw_due, Convention.MODIFIED_FOLLOWING)
        days = (due - previous).days if index else 0
        interest = accrued_interest(balance, rate_bps, days)
        balance = balance - instalment
        rows.append(
            AmortisationRow(
                due_date=due,
                principal=instalment,
                interest=interest,
                closing_balance=balance,
            )
        )
        previous = due
    return tuple(rows)


def total_capacity(
    facilities: tuple[Facility, ...],
    policy: TreasuryPolicy,
) -> tuple[Money, Money]:
    """`(undrawn, max_safe_draw)` summed across facilities in one currency."""
    if not facilities:
        raise PolicyError("total_capacity requires at least one facility")
    currencies = {facility.currency for facility in facilities}
    if len(currencies) != 1:
        raise PolicyError(f"facilities span multiple currencies: {sorted(currencies)}")
    currency = facilities[0].limit.currency
    undrawn = Money.sum((facility.undrawn for facility in facilities), currency)
    safe = Money.sum((max_safe_draw(facility, policy) for facility in facilities), currency)
    return undrawn, safe


__all__ = [
    "BPS",
    "DAY_COUNT_BASIS",
    "AmortisationRow",
    "Facility",
    "accrued_interest",
    "all_in_draw_cost_bps",
    "amortisation_schedule",
    "capacity",
    "commitment_fee",
    "draw_cost",
    "effective_utilization_ceiling",
    "max_safe_draw",
    "total_capacity",
    "utilization_bps",
]
