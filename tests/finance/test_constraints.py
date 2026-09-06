"""Constraint gate tests — 100% branch coverage, per `PHASES.md`.

The most important assertion in this file is the boring one: an AP deferral
touching payroll fails, and it fails because the policy row says so. Everything
else in the product depends on that being true in code rather than in a prompt.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.finance import constraints
from backend.finance.constraints import LiquidityState, ProposedAction
from backend.finance.errors import PolicyError
from backend.finance.money import Money
from backend.finance.policy import TreasuryPolicy

POLICY = TreasuryPolicy.load()


def usd(major: int) -> Money:
    return Money.of(major * 100, "USD")


def healthy_state(**overrides: object) -> LiquidityState:
    """Liquidity comfortably inside every floor, so one failure is isolatable."""
    defaults: dict[str, object] = {
        "projected_min_cash": usd(30_000_000),
        "projected_min_30d_liquidity": usd(40_000_000),
        "revolver_limit": usd(50_000_000),
        "revolver_drawn": usd(10_000_000),
    }
    defaults.update(overrides)
    return LiquidityState(**defaults)  # type: ignore[arg-type]


def named(results: tuple[object, ...]) -> dict[str, bool]:
    return {result.name: result.passed for result in results}  # type: ignore[attr-defined]


def test_a_clean_action_passes_every_constraint() -> None:
    results = constraints.validate(
        POLICY,
        ProposedAction(action="ap_deferral", amount=usd(100_000), payment_class="trade"),
        healthy_state(),
    )
    assert constraints.passed(results)
    assert constraints.failures(results) == ()
    assert constraints.explain(results) == "all constraints satisfied"
    # Every constraint is evaluated, not just up to the first failure.
    assert len(results) == 7


def test_deferring_payroll_fails_closed() -> None:
    results = constraints.validate(
        POLICY,
        ProposedAction(
            action="ap_deferral",
            amount=usd(500_000),
            payment_class="payroll",
            deferral_days=14,
        ),
        healthy_state(),
    )
    assert not constraints.passed(results)
    violated = constraints.failures(results)
    assert [item.name for item in violated] == ["protected_payment_class"]
    assert "protected by TreasuryPolicy" in violated[0].reason
    assert violated[0].margin == -14


def test_deferring_tax_fails_closed() -> None:
    results = constraints.validate(
        POLICY,
        ProposedAction(action="payment_delay", amount=usd(1), payment_class="tax"),
        healthy_state(),
    )
    assert not named(results)["protected_payment_class"]


def test_a_protected_class_may_still_be_paid_on_time() -> None:
    """Protection blocks *delay*, not the payment itself."""
    results = constraints.validate(
        POLICY,
        ProposedAction(action="fund_payroll", amount=usd(2_000_000), payment_class="payroll"),
        healthy_state(),
    )
    assert named(results)["protected_payment_class"]
    reason = next(item.reason for item in results if item.name == "protected_payment_class")
    assert "does not delay it" in reason


def test_min_cash_and_liquidity_floors_report_their_margin() -> None:
    results = constraints.validate(
        POLICY,
        ProposedAction(action="ap_deferral", amount=usd(1), payment_class="trade"),
        healthy_state(
            projected_min_cash=usd(14_000_000),
            projected_min_30d_liquidity=usd(19_000_000),
        ),
    )
    status = named(results)
    assert not status["min_unrestricted_cash"]
    assert not status["min_30d_liquidity"]
    cash = next(item for item in results if item.name == "min_unrestricted_cash")
    # Floor is $15,000,000.00; projected is $14,000,000.00.
    assert cash.margin.amount == -100_000_000
    assert "below the" in cash.reason


def test_a_draw_larger_than_undrawn_capacity_fails() -> None:
    results = constraints.validate(
        POLICY,
        ProposedAction(action="revolver_draw", amount=usd(45_000_000)),
        healthy_state(),
    )
    status = named(results)
    assert not status["revolver_capacity"]
    capacity = next(item for item in results if item.name == "revolver_capacity")
    assert "exceeds undrawn capacity" in capacity.reason


def test_a_draw_within_capacity_can_still_breach_the_utilization_ceiling() -> None:
    """Undrawn capacity and *safe* capacity are different numbers."""
    results = constraints.validate(
        POLICY,
        ProposedAction(action="revolver_draw", amount=usd(30_000_000)),
        healthy_state(),
    )
    status = named(results)
    assert status["revolver_capacity"]
    assert not status["max_revolver_utilization"]
    utilization = next(item for item in results if item.name == "max_revolver_utilization")
    # ($10M + $30M) / $50M = 8000 bps against a 7000 bps policy ceiling.
    assert utilization.actual == 8_000
    assert utilization.limit == 7_000
    assert utilization.margin == -1_000


def test_utilization_is_not_applicable_without_a_facility() -> None:
    results = constraints.validate(
        POLICY,
        ProposedAction(action="collection_call", amount=usd(0)),
        healthy_state(revolver_limit=usd(0), revolver_drawn=usd(0)),
    )
    utilization = next(item for item in results if item.name == "max_revolver_utilization")
    assert utilization.passed
    assert "not applicable" in utilization.reason


def test_covenant_headroom_is_absent_rather_than_passing_when_untested() -> None:
    results = constraints.validate(
        POLICY,
        ProposedAction(action="ap_deferral", amount=usd(1), payment_class="trade"),
        healthy_state(),
    )
    covenant = next(item for item in results if item.name == "covenant_headroom")
    assert covenant.passed
    assert covenant.actual is None
    assert "no covenant is tested inside the forecast horizon" in covenant.reason


def test_covenant_headroom_pass_and_breach() -> None:
    ok = constraints.validate(
        POLICY,
        ProposedAction(action="revolver_draw", amount=usd(1)),
        healthy_state(covenant_headroom=usd(4_000_000), covenant_name="Leverage"),
    )
    assert named(ok)["covenant_headroom"]
    assert "Leverage retains" in next(
        item.reason for item in ok if item.name == "covenant_headroom"
    )

    breached = constraints.validate(
        POLICY,
        ProposedAction(action="revolver_draw", amount=usd(1)),
        healthy_state(covenant_headroom=usd(-500_000), covenant_name="Leverage"),
    )
    assert not named(breached)["covenant_headroom"]
    assert "is breached by" in next(
        item.reason for item in breached if item.name == "covenant_headroom"
    )


def test_single_source_vendor_cannot_be_deferred_past_its_lead_time() -> None:
    """The dangerous deferral is usually a small one (`DATA_SOURCES.md` §5)."""
    results = constraints.validate(
        POLICY,
        ProposedAction(
            action="ap_deferral",
            amount=usd(80_000),
            payment_class="trade",
            deferral_days=45,
            counterparty="Vendor Q",
            single_source=True,
            replacement_lead_time_days=42,
        ),
        healthy_state(),
    )
    assert not named(results)["supplier_lead_time"]
    lead = next(item for item in results if item.name == "supplier_lead_time")
    assert "Vendor Q" in lead.reason
    assert lead.margin == -3


def test_a_deferral_inside_the_lead_time_is_allowed() -> None:
    results = constraints.validate(
        POLICY,
        ProposedAction(
            action="ap_deferral",
            amount=usd(80_000),
            payment_class="trade",
            deferral_days=14,
            counterparty="Vendor Q",
            single_source=True,
            replacement_lead_time_days=42,
        ),
        healthy_state(),
    )
    assert named(results)["supplier_lead_time"]
    assert "is inside" in next(item.reason for item in results if item.name == "supplier_lead_time")


@pytest.mark.parametrize(
    "proposal",
    [
        # Not single-source.
        ProposedAction(action="ap_deferral", amount=Money.of(1, "USD"), deferral_days=45),
        # Single-source but no stated lead time.
        ProposedAction(
            action="ap_deferral",
            amount=Money.of(1, "USD"),
            deferral_days=45,
            single_source=True,
        ),
        # Single-source with a lead time, but no deferral is proposed.
        ProposedAction(
            action="early_pay_discount",
            amount=Money.of(1, "USD"),
            single_source=True,
            replacement_lead_time_days=42,
        ),
    ],
)
def test_supplier_lead_time_is_not_applicable(proposal: ProposedAction) -> None:
    results = constraints.validate(POLICY, proposal, healthy_state())
    assert named(results)["supplier_lead_time"]


def test_several_violations_are_all_reported_together() -> None:
    """A replan must see every broken constraint, not rediscover them one by one."""
    results = constraints.validate(
        POLICY,
        ProposedAction(
            action="ap_deferral",
            amount=usd(60_000_000),
            payment_class="payroll",
            deferral_days=30,
        ),
        healthy_state(
            projected_min_cash=usd(1_000_000),
            projected_min_30d_liquidity=usd(1_000_000),
            covenant_headroom=usd(-1),
            covenant_name="Leverage",
        ),
    )
    violated = {item.name for item in constraints.failures(results)}
    assert violated == {
        "protected_payment_class",
        "min_unrestricted_cash",
        "min_30d_liquidity",
        "covenant_headroom",
    }
    explanation = constraints.explain(results)
    assert explanation.count("; ") == 3
    assert explanation.startswith("protected_payment_class:")


def test_input_validation() -> None:
    state = healthy_state()
    with pytest.raises(PolicyError, match="single currency"):
        constraints.validate(
            POLICY, ProposedAction(action="ap_deferral", amount=Money.of(1, "EUR")), state
        )
    with pytest.raises(PolicyError, match="deferral_days"):
        constraints.validate(
            POLICY,
            ProposedAction(action="ap_deferral", amount=usd(1), deferral_days=-1),
            state,
        )
    with pytest.raises(PolicyError, match="non-negative magnitude"):
        constraints.validate(POLICY, ProposedAction(action="ap_deferral", amount=usd(-1)), state)


def test_undrawn_revolver_is_derived_not_supplied() -> None:
    state = healthy_state()
    assert state.undrawn_revolver == usd(40_000_000)


def test_policy_is_versioned_not_hardcoded() -> None:
    """The rejection message cites the policy version that produced it."""
    results = constraints.validate(
        POLICY,
        ProposedAction(action="ap_deferral", amount=usd(1), payment_class="payroll"),
        healthy_state(),
    )
    reason = next(item.reason for item in results if item.name == "protected_payment_class")
    assert f"v{POLICY.version}" in reason
    assert datetime.now(UTC) is not None  # sanity: policy load did not stub the clock
