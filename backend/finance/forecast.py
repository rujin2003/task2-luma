"""The 13-week rolling direct-method cash forecast.

This is the deliverable the product exists to produce (`WORKFLOW.md` §1) and the
only place in the system where a forecast number is created. Agents read it; they
never compute it.

**Direct method.** Actual cash in and out by category, not net income adjusted
for non-cash items. Indirect-method forecasting is a planning tool; treasury runs
direct, and the two are not interchangeable.

**Fixed category set.** `backend.models.vocab.FORECAST_CATEGORIES`, closed. A
forecast whose categories move cannot be variance-analysed against its own
history, so every version emits a cell for every category in every week — 14 × 13
— including the zero ones. A missing cell and a zero cell mean different things
to a variance bridge.

**Every line carries its own justification.** Source, assumption, method and
as-of are per-cell, not per-forecast, because "why is week 6 AP $2.4M" is the
question actually asked, and the answer differs from the answer for week 2.

**Drivers produce dates, not averages.** Every builder below goes through
`cadence.py`. Payroll lands on pay dates, AP on run days, rent on the 1st, tax on
the quarterly dates, subscriptions on their anniversary — and Dodo receipts land
on the *payout* date, not the payment date, because a successful payment is not
cash (`DATA_SOURCES.md` §6, Insight #2).

**Rolling.** `roll_forward` drops week 1 and adds week 14. The dropped week
becomes the actual that next cycle's variance bridge is measured against.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

from backend.contracts.common import MoneyDTO
from backend.contracts.forecast import ForecastGrid, ForecastLineDTO
from backend.finance import cadence
from backend.finance.cadence import (
    BusinessCalendar,
    Convention,
    DepositSchedule,
    PayrollFrequency,
    ServiceFrequency,
)
from backend.finance.errors import PolicyError
from backend.finance.money import Money
from backend.finance.provenance import Provenance

BPS = 10_000
HORIZON_WEEKS = 13

#: The closed category set. Duplicated from `backend.models.vocab` rather than
#: imported: `backend/finance` is a pure computation layer with no dependency on
#: the ORM, and a test asserts the two lists are identical so they cannot drift.
CATEGORIES: tuple[str, ...] = (
    "receipts_trade_ar",
    "receipts_subscription",
    "receipts_other",
    "payroll_salaried",
    "payroll_hourly",
    "payroll_taxes_benefits",
    "ap_trade",
    "rent_leases",
    "tax_income_estimated",
    "tax_sales_vat",
    "debt_interest",
    "debt_principal",
    "capex",
    "insurance_software",
)

INFLOW_CATEGORIES: frozenset[str] = frozenset(
    {"receipts_trade_ar", "receipts_subscription", "receipts_other"}
)

#: AR aging buckets, in days past due. The last bucket is open-ended.
AGING_BUCKETS: tuple[str, ...] = ("current", "1-30", "31-60", "61-90", "90+")


def aging_bucket(days_past_due: int) -> str:
    """Standard AR aging bucket for a days-past-due count."""
    if days_past_due <= 0:
        return "current"
    if days_past_due <= 30:
        return "1-30"
    if days_past_due <= 60:
        return "31-60"
    if days_past_due <= 90:
        return "61-90"
    return "90+"


# --------------------------------------------------------------------------- #
# Driver inputs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DatedAmount:
    """A committed, dated cash movement. Signed: positive is cash in."""

    reference: str
    amount: Money
    due_date: date
    description: str = ""
    source_table: str = "calendar_events"


@dataclass(frozen=True, slots=True)
class LagComponent:
    """One mode of a payment-delay mixture: a weight and a lag in days."""

    weight_bps: int
    lag_days: int


@dataclass(frozen=True, slots=True)
class CollectionCurve:
    """Empirical collection behaviour, fitted offline from real AR datasets.

    Two things here are deliberate. First, the lag is a **mixture**, not a mean:
    real invoice payment delay is strongly bimodal — most invoices pay within a
    few days of terms and a long tail runs 30-90+ days late (`DATA_SOURCES.md`
    §4). Forecasting the mean puts cash in a week where none arrives. Second,
    collection *probability* is separate from collection *timing*, because an
    invoice that will never be paid and an invoice that will be paid late are
    different problems with different worklist rows.
    """

    #: Segment → mixture components. `"default"` is the required fallback.
    lag_mixture: dict[str, tuple[LagComponent, ...]]
    #: Aging bucket → probability the open balance is collected at all, in bps.
    collection_probability_bps: dict[str, int]
    #: Multiplier applied to a disputed invoice's probability, in bps.
    dispute_haircut_bps: int = BPS

    def __post_init__(self) -> None:
        if "default" not in self.lag_mixture:
            raise PolicyError("CollectionCurve needs a 'default' lag mixture")
        for segment, components in self.lag_mixture.items():
            if not components:
                raise PolicyError(f"lag mixture for {segment!r} is empty")
            if sum(component.weight_bps for component in components) != BPS:
                raise PolicyError(f"lag mixture for {segment!r} does not sum to 10000 bps")
        missing = set(AGING_BUCKETS) - set(self.collection_probability_bps)
        if missing:
            raise PolicyError(f"collection probabilities missing buckets: {sorted(missing)}")

    def mixture_for(self, segment: str | None) -> tuple[LagComponent, ...]:
        if segment is not None and segment in self.lag_mixture:
            return self.lag_mixture[segment]
        return self.lag_mixture["default"]

    def probability_bps(self, bucket: str, *, disputed: bool) -> int:
        base = self.collection_probability_bps[bucket]
        if not disputed:
            return base
        return base * self.dispute_haircut_bps // BPS


@dataclass(frozen=True, slots=True)
class OpenInvoice:
    """An open trade receivable, as the AR driver sees it."""

    invoice_ref: str
    customer: str
    open_amount: Money
    due_date: date
    segment: str | None = None
    disputed: bool = False
    #: A stated promise-to-pay overrides the fitted curve entirely — a human
    #: commitment from the counterparty is better evidence than a distribution.
    promise_to_pay: date | None = None


@dataclass(frozen=True, slots=True)
class SubscriptionBilling:
    """A recurring charge billed through the payment processor."""

    subscription_ref: str
    customer: str
    amount: Money
    next_billing_date: date
    interval: ServiceFrequency = ServiceFrequency.MONTHLY
    status: str = "active"


@dataclass(frozen=True, slots=True)
class PayrollPlan:
    """Headcount cost expressed as dates and amounts, never as a monthly rate."""

    salaried_gross_per_period: Money
    hourly_gross_per_period: Money
    frequency: PayrollFrequency = PayrollFrequency.SEMI_MONTHLY
    #: Required when `frequency` is biweekly; ignored otherwise.
    salaried_anchor: date | None = None
    #: Hourly payroll runs biweekly one week in arrears (`WORKFLOW.md` §3).
    hourly_anchor: date | None = None
    #: Employer-side taxes as a proportion of gross.
    employer_tax_bps: int = 0
    #: Employee withholding remitted with the same deposit.
    employee_withholding_bps: int = 0
    benefits_per_period: Money | None = None
    deposit_schedule: DepositSchedule = DepositSchedule.SEMI_WEEKLY


@dataclass(frozen=True, slots=True)
class OpenPayable:
    """An open trade payable awaiting a payment run."""

    invoice_ref: str
    vendor: str
    open_amount: Money
    due_date: date
    payment_class: str = "trade"
    criticality: str = "standard"
    single_source: bool = False
    replacement_lead_time_days: int | None = None
    early_pay_discount_bps: int = 0
    early_pay_days: int | None = None


@dataclass(frozen=True, slots=True)
class DebtServicePlan:
    """Scheduled interest and principal on one facility."""

    facility_id: str
    name: str
    drawn: Money
    all_in_rate_bps: int
    first_service_date: date
    frequency: ServiceFrequency = ServiceFrequency.QUARTERLY
    #: Straight-line principal instalments, if the facility amortises.
    principal_per_period: Money | None = None


@dataclass(frozen=True, slots=True)
class ForecastOverride:
    """A human adjustment to one cell, with a stated reason.

    An overridden assumption is *better* data than a generated one
    (`WORKFLOW.md` §4), so an override is a first-class recorded object and the
    cell it replaces carries `method='override'` and the author's reason forward
    into the accuracy statistics.
    """

    category: str
    week_index: int
    amount: Money
    reason: str
    author: str

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise PolicyError(f"override targets unknown category {self.category!r}")
        if self.week_index < 1:
            raise PolicyError("override week_index is 1-based")


@dataclass(frozen=True, slots=True)
class ForecastInputs:
    """Everything the engine needs, and nothing it does not.

    Deliberately a plain value object with no database handle: the engine is a
    pure function of its drivers, which is what makes the determinism harness in
    Phase 11 possible and the golden-file tests meaningful.
    """

    version_id: str
    as_of: datetime
    first_week_start: date
    opening_cash: Money
    calendar: BusinessCalendar
    undrawn_revolver: Money
    horizon_weeks: int = HORIZON_WEEKS

    open_invoices: tuple[OpenInvoice, ...] = ()
    collection_curve: CollectionCurve | None = None
    subscriptions: tuple[SubscriptionBilling, ...] = ()
    #: Rolling processor success rate. Failed charges are simply not forecast as
    #: cash; the recoverable portion is the Dodo agent's worklist, not a forecast
    #: line, because retrying is an action somebody has to take.
    processor_success_rate_bps: int = BPS
    #: Payment → balance ledger → payout. The lag that moves 30-day liquidity.
    processor_payout_lag_days: int = 2
    other_receipts: tuple[DatedAmount, ...] = ()

    payroll: PayrollPlan | None = None
    open_payables: tuple[OpenPayable, ...] = ()
    ap_run_weekday: int = cadence.THURSDAY
    rent_per_month: Money | None = None
    rent_day_of_month: int = 1
    estimated_tax_per_quarter: Money | None = None
    sales_tax: tuple[DatedAmount, ...] = ()
    debt_service: tuple[DebtServicePlan, ...] = ()
    capex: tuple[DatedAmount, ...] = ()
    insurance_software: tuple[DatedAmount, ...] = ()

    overrides: tuple[ForecastOverride, ...] = ()

    def __post_init__(self) -> None:
        if self.horizon_weeks < 1:
            raise PolicyError("horizon_weeks must be positive")
        if self.first_week_start.weekday() != cadence.MONDAY:
            raise PolicyError(f"{self.first_week_start} is not a Monday")
        if self.opening_cash.currency.code != self.undrawn_revolver.currency.code:
            raise PolicyError("opening cash and revolver capacity must share a currency")

    @property
    def currency(self) -> str:
        return self.opening_cash.currency.code

    @property
    def horizon_start(self) -> date:
        return self.first_week_start

    @property
    def horizon_end(self) -> date:
        return self.first_week_start + timedelta(weeks=self.horizon_weeks) - timedelta(days=1)


# --------------------------------------------------------------------------- #
# Engine output
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Contribution:
    """One driver row's share of a forecast cell — the drill-down target."""

    reference: str
    description: str
    amount: Money
    event_date: date
    source_table: str


@dataclass(frozen=True, slots=True)
class ForecastCell:
    """One category × week, with its justification and its inputs."""

    category: str
    week_index: int
    week_start: date
    week_end: date
    amount: Money
    source: str
    assumption: str
    method: str
    contributions: tuple[Contribution, ...] = ()
    override_reason: str | None = None

    def provenance(self, as_of: datetime, retrieved_at: datetime) -> Provenance:
        """Point at the largest contributing source row, or at the cell itself.

        A cell built from thirty invoices cannot name one source row honestly, so
        it names the dominant one and exposes the rest through `contributions`.
        An empty cell is provenanced to the engine's own computation, which is
        the truthful answer to "where did this zero come from".
        """
        if self.contributions:
            dominant = max(
                self.contributions, key=lambda item: (abs(item.amount.amount), item.reference)
            )
            return Provenance(
                source_system="engine",
                source_table=dominant.source_table,
                source_pk=dominant.reference,
                field="amount_minor",
                as_of=as_of,
                retrieved_at=retrieved_at,
            )
        return Provenance(
            source_system="engine",
            source_table="forecast_lines",
            source_pk=f"{self.category}|{self.week_start.isoformat()}",
            field="amount_minor",
            as_of=as_of,
            retrieved_at=retrieved_at,
        )


@dataclass(frozen=True, slots=True)
class Forecast:
    """A complete 13-week grid plus the running balances derived from it."""

    version_id: str
    as_of: datetime
    currency: str
    opening_cash: Money
    weeks: tuple[date, ...]
    cells: tuple[ForecastCell, ...]
    closing_cash_by_week: tuple[Money, ...]
    available_liquidity_by_week: tuple[Money, ...]
    undrawn_revolver: Money

    def cell(self, category: str, week_index: int) -> ForecastCell:
        for item in self.cells:
            if item.category == category and item.week_index == week_index:
                return item
        raise KeyError(f"no cell for {category!r} week {week_index}")

    def category_total(self, category: str) -> Money:
        return Money.sum(
            (item.amount for item in self.cells if item.category == category), self.currency
        )

    def week_total(self, week_index: int) -> Money:
        return Money.sum(
            (item.amount for item in self.cells if item.week_index == week_index), self.currency
        )

    def net_change(self) -> Money:
        return Money.sum((item.amount for item in self.cells), self.currency)

    def minimum_cash(self) -> Money:
        """The trough of the projected balance — what the policy check reads."""
        return min(self.closing_cash_by_week)

    def minimum_cash_week(self) -> int:
        """1-based week of the trough. A breach is dated, never a vibe."""
        trough = self.minimum_cash()
        return self.closing_cash_by_week.index(trough) + 1

    def minimum_liquidity(self) -> Money:
        return min(self.available_liquidity_by_week)

    def liquidity_within(self, days: int) -> Money:
        """Minimum available liquidity inside the next `days` days.

        Weeks are whole buckets, so a 30-day window covers the first four full
        weeks and any partial fifth week is excluded rather than pro-rated —
        a pro-rated liquidity floor is a number nobody can reproduce by hand.
        """
        if days < 7:
            raise PolicyError("liquidity window must cover at least one week")
        weeks = min(days // 7, len(self.available_liquidity_by_week))
        return min(self.available_liquidity_by_week[:weeks])

    def to_grid(self, *, retrieved_at: datetime | None = None) -> ForecastGrid:
        """The frozen contract view the agents consume."""
        stamp = retrieved_at if retrieved_at is not None else self.as_of
        lines = tuple(
            ForecastLineDTO(
                category=item.category,
                week_start=item.week_start,
                week_end=item.week_end,
                amount=MoneyDTO.from_money(item.amount),
                source=item.source,
                assumption=item.assumption,
                method=item.method,
                as_of=self.as_of,
                provenance=item.provenance(self.as_of, stamp),
            )
            for item in self.cells
        )
        return ForecastGrid(
            version_id=self.version_id,
            as_of=self.as_of,
            opening_cash=MoneyDTO.from_money(self.opening_cash),
            weeks=self.weeks,
            lines=lines,
            closing_cash_by_week=tuple(
                MoneyDTO.from_money(value) for value in self.closing_cash_by_week
            ),
            available_liquidity_by_week=tuple(
                MoneyDTO.from_money(value) for value in self.available_liquidity_by_week
            ),
        )


# --------------------------------------------------------------------------- #
# Drafts — one per dated cash movement, before weekly aggregation
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Draft:
    category: str
    event_date: date
    amount: Money
    reference: str
    description: str
    source: str
    assumption: str
    method: str
    source_table: str


@dataclass(slots=True)
class _DraftBuilder:
    inputs: ForecastInputs
    drafts: list[_Draft] = field(default_factory=list)

    @property
    def zero(self) -> Money:
        return Money.zero(self.inputs.opening_cash.currency)

    def add(
        self,
        *,
        category: str,
        event_date: date,
        amount: Money,
        reference: str,
        description: str,
        source: str,
        assumption: str,
        method: str,
        source_table: str,
    ) -> None:
        """Record a dated movement, discarding anything outside the horizon."""
        if amount.amount == 0:
            return
        if not self.inputs.horizon_start <= event_date <= self.inputs.horizon_end:
            return
        self.drafts.append(
            _Draft(
                category=category,
                event_date=event_date,
                amount=amount,
                reference=reference,
                description=description,
                source=source,
                assumption=assumption,
                method=method,
                source_table=source_table,
            )
        )


# --------------------------------------------------------------------------- #
# Driver builders
# --------------------------------------------------------------------------- #


def _build_trade_receipts(builder: _DraftBuilder) -> None:
    """Customer receipts: aging bucket × collection curve, invoice by invoice."""
    inputs = builder.inputs
    curve = inputs.collection_curve
    if curve is None or not inputs.open_invoices:
        return
    as_of_date = inputs.as_of.date()
    for invoice in inputs.open_invoices:
        if invoice.open_amount.amount <= 0:
            continue
        bucket = aging_bucket((as_of_date - invoice.due_date).days)
        probability = curve.probability_bps(bucket, disputed=invoice.disputed)
        expected = Money.of(
            invoice.open_amount.amount * probability // BPS, invoice.open_amount.currency
        )
        if expected.amount == 0:
            continue

        if invoice.promise_to_pay is not None:
            landing = inputs.calendar.adjust(invoice.promise_to_pay, Convention.FOLLOWING)
            builder.add(
                category="receipts_trade_ar",
                event_date=landing,
                amount=expected,
                reference=invoice.invoice_ref,
                description=f"{invoice.customer} — promise to pay",
                source="ar_aging",
                assumption=(
                    f"{invoice.customer} promised payment on "
                    f"{invoice.promise_to_pay.isoformat()}; {bucket} bucket, "
                    f"{probability} bps collectible"
                ),
                method="promise_to_pay",
                source_table="invoices",
            )
            continue

        components = curve.mixture_for(invoice.segment)
        parts = expected.allocate([component.weight_bps for component in components])
        for component, part in zip(components, parts, strict=True):
            landing = inputs.calendar.adjust(
                invoice.due_date + timedelta(days=component.lag_days), Convention.FOLLOWING
            )
            builder.add(
                category="receipts_trade_ar",
                event_date=landing,
                amount=part,
                reference=invoice.invoice_ref,
                description=f"{invoice.customer} — {component.lag_days}d lag mode",
                source="ar_aging",
                assumption=(
                    f"{bucket} bucket at {probability} bps collectible; "
                    f"{component.weight_bps} bps of the balance pays "
                    f"{component.lag_days} days after terms"
                ),
                method="collection_curve",
                source_table="invoices",
            )


def _interval_months(interval: ServiceFrequency) -> int:
    return {
        ServiceFrequency.MONTHLY: 1,
        ServiceFrequency.QUARTERLY: 3,
        ServiceFrequency.SEMIANNUAL: 6,
        ServiceFrequency.ANNUAL: 12,
    }[interval]


def _build_subscription_receipts(builder: _DraftBuilder) -> None:
    """Processor receipts, landing on the payout date rather than the charge date.

    The payment → balance-ledger → payout lag is modelled explicitly. Treating a
    successful charge as immediate cash is the single most common error in this
    integration and it lands the money in the wrong week, which is exactly the
    week the 30-day liquidity test reads.
    """
    inputs = builder.inputs
    if not inputs.subscriptions:
        return
    success = inputs.processor_success_rate_bps
    for subscription in inputs.subscriptions:
        if subscription.status != "active" or subscription.amount.amount <= 0:
            continue
        step = _interval_months(subscription.interval)
        billing = subscription.next_billing_date
        while billing <= inputs.horizon_end:
            if billing >= inputs.horizon_start - timedelta(days=14):
                expected = Money.of(
                    subscription.amount.amount * success // BPS, subscription.amount.currency
                )
                payout = cadence.settlement_date(
                    billing,
                    inputs.calendar,
                    lag_business_days=inputs.processor_payout_lag_days,
                )
                builder.add(
                    category="receipts_subscription",
                    event_date=payout,
                    amount=expected,
                    reference=subscription.subscription_ref,
                    description=f"{subscription.customer} — billed {billing.isoformat()}",
                    source="dodo",
                    assumption=(
                        f"charge on {billing.isoformat()} at {success} bps rolling success "
                        f"rate, settling {inputs.processor_payout_lag_days} business days "
                        "later at payout"
                    ),
                    method="billing_schedule_x_success_rate",
                    source_table="subscriptions",
                )
            billing = cadence.add_months(billing, step)


def _build_other_receipts(builder: _DraftBuilder) -> None:
    for item in builder.inputs.other_receipts:
        builder.add(
            category="receipts_other",
            event_date=item.due_date,
            amount=item.amount,
            reference=item.reference,
            description=item.description or "other receipt",
            source="calendar",
            assumption=item.description or "dated one-off receipt",
            method="dated_commitment",
            source_table=item.source_table,
        )


def _build_payroll(builder: _DraftBuilder) -> None:
    """Salaried, hourly and the separately-dated tax deposit."""
    inputs = builder.inputs
    plan = inputs.payroll
    if plan is None:
        return

    window_start = inputs.horizon_start - timedelta(days=45)
    salaried_dates = cadence.pay_dates(
        plan.frequency,
        window_start,
        inputs.horizon_end,
        inputs.calendar,
        anchor=plan.salaried_anchor,
    )
    for pay_day in salaried_dates:
        builder.add(
            category="payroll_salaried",
            event_date=pay_day,
            amount=-plan.salaried_gross_per_period,
            reference=f"payroll-salaried-{pay_day.isoformat()}",
            description="salaried payroll",
            source="payroll_calendar",
            assumption=(
                f"{plan.frequency} salaried payroll of "
                f"{plan.salaried_gross_per_period} funded on {pay_day.isoformat()}"
            ),
            method="pay_date_schedule",
            source_table="calendar_events",
        )

    hourly_dates: tuple[date, ...] = ()
    if plan.hourly_gross_per_period.amount > 0:
        hourly_anchor = plan.hourly_anchor or plan.salaried_anchor
        if hourly_anchor is None:
            raise PolicyError("hourly payroll requires an anchor pay date")
        hourly_dates = cadence.biweekly_pay_dates(
            hourly_anchor, window_start, inputs.horizon_end, inputs.calendar
        )
        for pay_day in hourly_dates:
            builder.add(
                category="payroll_hourly",
                event_date=pay_day,
                amount=-plan.hourly_gross_per_period,
                reference=f"payroll-hourly-{pay_day.isoformat()}",
                description="hourly payroll, one week in arrears",
                source="payroll_calendar",
                assumption=(
                    f"biweekly hourly payroll of {plan.hourly_gross_per_period} for the "
                    "period ending one week before the pay date"
                ),
                method="pay_date_schedule",
                source_table="calendar_events",
            )

    # Payroll taxes are not payroll: separate line, separate date, IRS deposit
    # schedule driven by a lookback period (`WORKFLOW.md` §3).
    tax_bps = plan.employer_tax_bps + plan.employee_withholding_bps
    if tax_bps > 0:
        gross_by_date: dict[date, Money] = {}
        for pay_day in salaried_dates:
            gross_by_date[pay_day] = (
                gross_by_date.get(pay_day, builder.zero) + plan.salaried_gross_per_period
            )
        for pay_day in hourly_dates:
            gross_by_date[pay_day] = (
                gross_by_date.get(pay_day, builder.zero) + plan.hourly_gross_per_period
            )

        for pay_day, gross in sorted(gross_by_date.items()):
            deposit_dates = cadence.payroll_tax_deposit_dates(
                (pay_day,), inputs.calendar, schedule=plan.deposit_schedule
            )
            tax = Money.of(gross.amount * tax_bps // BPS, gross.currency)
            for deposit in deposit_dates:
                builder.add(
                    category="payroll_taxes_benefits",
                    event_date=deposit,
                    amount=-tax,
                    reference=f"payroll-tax-{deposit.isoformat()}",
                    description=f"employment tax deposit for payroll {pay_day.isoformat()}",
                    source="payroll_calendar",
                    assumption=(
                        f"{tax_bps} bps of {gross} gross paid {pay_day.isoformat()}, "
                        f"deposited on the {plan.deposit_schedule} schedule"
                    ),
                    method="irs_deposit_schedule",
                    source_table="calendar_events",
                )

    if plan.benefits_per_period is not None and plan.benefits_per_period.amount > 0:
        for pay_day in salaried_dates:
            builder.add(
                category="payroll_taxes_benefits",
                event_date=pay_day,
                amount=-plan.benefits_per_period,
                reference=f"benefits-{pay_day.isoformat()}",
                description="benefits funding",
                source="payroll_calendar",
                assumption=f"benefits of {plan.benefits_per_period} funded with payroll",
                method="pay_date_schedule",
                source_table="calendar_events",
            )


def _build_ap(builder: _DraftBuilder) -> None:
    """AP is a batch event on run days, never a smooth outflow."""
    inputs = builder.inputs
    if not inputs.open_payables:
        return
    runs = cadence.ap_run_dates(
        inputs.horizon_start,
        inputs.horizon_end,
        inputs.calendar,
        weekday=inputs.ap_run_weekday,
    )
    if not runs:
        return
    for payable in inputs.open_payables:
        if payable.open_amount.amount <= 0:
            continue
        # Paid in the first run on or after the due date; anything already due
        # catches the next run rather than being smeared backwards.
        candidates = [run for run in runs if run >= payable.due_date]
        run_date = candidates[0] if candidates else runs[0]
        builder.add(
            category="ap_trade",
            event_date=run_date,
            amount=-payable.open_amount,
            reference=payable.invoice_ref,
            description=f"{payable.vendor} — AP run",
            source="ap_open_items",
            assumption=(
                f"{payable.vendor} invoice due {payable.due_date.isoformat()} paid in the "
                f"{run_date.isoformat()} payment run"
            ),
            method="payment_run_schedule",
            source_table="vendor_invoices",
        )


def _build_rent(builder: _DraftBuilder) -> None:
    inputs = builder.inputs
    if inputs.rent_per_month is None or inputs.rent_per_month.amount == 0:
        return
    for due in cadence.monthly_dates(
        inputs.horizon_start,
        inputs.horizon_end,
        inputs.calendar,
        day_of_month=inputs.rent_day_of_month,
    ):
        builder.add(
            category="rent_leases",
            event_date=due,
            amount=-inputs.rent_per_month,
            reference=f"rent-{due.isoformat()}",
            description="rent and leases",
            source="contracts",
            assumption=f"contracted rent of {inputs.rent_per_month} due on the 1st",
            method="contractual",
            source_table="calendar_events",
        )


def _build_taxes(builder: _DraftBuilder) -> None:
    inputs = builder.inputs
    if inputs.estimated_tax_per_quarter is not None and (
        inputs.estimated_tax_per_quarter.amount > 0
    ):
        for due in cadence.quarterly_estimated_tax_dates(
            inputs.horizon_start, inputs.horizon_end, inputs.calendar
        ):
            builder.add(
                category="tax_income_estimated",
                event_date=due,
                amount=-inputs.estimated_tax_per_quarter,
                reference=f"est-tax-{due.isoformat()}",
                description="quarterly estimated income tax",
                source="tax_calendar",
                assumption="prior-year safe harbour, one quarter",
                method="statutory_schedule",
                source_table="calendar_events",
            )
    for item in inputs.sales_tax:
        builder.add(
            category="tax_sales_vat",
            event_date=item.due_date,
            amount=item.amount,
            reference=item.reference,
            description=item.description or "sales tax remittance",
            source="tax_calendar",
            assumption=item.description or "sales tax on taxable sales",
            method="statutory_schedule",
            source_table=item.source_table,
        )


def _build_debt_service(builder: _DraftBuilder) -> None:
    """Interest accrued on the drawn balance, principal on the amortisation date."""
    inputs = builder.inputs
    for plan in inputs.debt_service:
        service_dates = cadence.debt_service_dates(
            plan.first_service_date,
            inputs.horizon_start,
            inputs.horizon_end,
            inputs.calendar,
            frequency=plan.frequency,
        )
        period_days = _interval_months(plan.frequency) * 30
        for due in service_dates:
            # Local import: `debt` imports `cadence`, and importing it at module
            # scope here would make the finance package's import graph cyclic.
            from backend.finance.debt import accrued_interest

            interest = accrued_interest(plan.drawn, plan.all_in_rate_bps, period_days)
            builder.add(
                category="debt_interest",
                event_date=due,
                amount=-interest,
                reference=f"{plan.facility_id}-interest-{due.isoformat()}",
                description=f"{plan.name} interest",
                source="debt_schedule",
                assumption=(
                    f"{plan.all_in_rate_bps} bps all-in on {plan.drawn} drawn, "
                    f"actual/360 over {period_days} days"
                ),
                method="facility_rate_x_drawn",
                source_table="debt_facilities",
            )
            if plan.principal_per_period is not None and plan.principal_per_period.amount > 0:
                builder.add(
                    category="debt_principal",
                    event_date=due,
                    amount=-plan.principal_per_period,
                    reference=f"{plan.facility_id}-principal-{due.isoformat()}",
                    description=f"{plan.name} principal",
                    source="debt_schedule",
                    assumption=f"{plan.frequency} amortisation instalment",
                    method="amortisation_schedule",
                    source_table="debt_facilities",
                )


def _build_dated(builder: _DraftBuilder, items: Sequence[DatedAmount], category: str) -> None:
    for item in items:
        builder.add(
            category=category,
            event_date=item.due_date,
            amount=item.amount,
            reference=item.reference,
            description=item.description or category,
            source="commitments",
            assumption=item.description or f"committed {category}",
            method="dated_commitment",
            source_table=item.source_table,
        )


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #

_EMPTY_ASSUMPTION: dict[str, str] = {
    "receipts_trade_ar": "no invoice is expected to collect in this week",
    "receipts_subscription": "no processor payout settles in this week",
    "receipts_other": "no other receipt is committed in this week",
    "payroll_salaried": "no salaried pay date falls in this week",
    "payroll_hourly": "no hourly pay date falls in this week",
    "payroll_taxes_benefits": "no employment tax deposit is due in this week",
    "ap_trade": "no payment run falls in this week",
    "rent_leases": "rent is due on the 1st; none falls in this week",
    "tax_income_estimated": "no quarterly estimated tax date falls in this week",
    "tax_sales_vat": "no sales tax remittance is due in this week",
    "debt_interest": "no interest payment date falls in this week",
    "debt_principal": "no principal instalment falls in this week",
    "capex": "no committed capex milestone falls in this week",
    "insurance_software": "no annual renewal falls in this week",
}


def build(inputs: ForecastInputs) -> Forecast:
    """Compute the grid. Deterministic: same inputs, same numbers, every run."""
    builder = _DraftBuilder(inputs=inputs)
    _build_trade_receipts(builder)
    _build_subscription_receipts(builder)
    _build_other_receipts(builder)
    _build_payroll(builder)
    _build_ap(builder)
    _build_rent(builder)
    _build_taxes(builder)
    _build_debt_service(builder)
    _build_dated(builder, inputs.capex, "capex")
    _build_dated(builder, inputs.insurance_software, "insurance_software")

    weeks = cadence.week_starts(inputs.first_week_start, inputs.horizon_weeks)
    currency = inputs.opening_cash.currency

    grouped: dict[tuple[str, int], list[_Draft]] = {}
    for draft in builder.drafts:
        index = cadence.week_index(draft.event_date, inputs.first_week_start)
        if not 1 <= index <= inputs.horizon_weeks:
            continue
        grouped.setdefault((draft.category, index), []).append(draft)

    override_map = {
        (override.category, override.week_index): override for override in inputs.overrides
    }
    for key in override_map:
        if key[1] > inputs.horizon_weeks:
            raise PolicyError(f"override for week {key[1]} is outside the horizon")

    cells: list[ForecastCell] = []
    for category in CATEGORIES:
        for index, week_start in enumerate(weeks, start=1):
            drafts = sorted(
                grouped.get((category, index), []),
                key=lambda draft: (draft.event_date, draft.reference),
            )
            amount = Money.sum((draft.amount for draft in drafts), currency)
            contributions = tuple(
                Contribution(
                    reference=draft.reference,
                    description=draft.description,
                    amount=draft.amount,
                    event_date=draft.event_date,
                    source_table=draft.source_table,
                )
                for draft in drafts
            )
            if drafts:
                source = drafts[0].source
                method = drafts[0].method
                assumption = _summarise_assumptions(drafts)
            else:
                source = "engine"
                method = "no_activity"
                assumption = _EMPTY_ASSUMPTION[category]

            override = override_map.get((category, index))
            override_reason: str | None = None
            if override is not None:
                if override.amount.currency.code != currency.code:
                    raise PolicyError(
                        f"override for {category} week {index} is in "
                        f"{override.amount.currency.code}, not {currency.code}"
                    )
                amount = override.amount
                source = "override"
                method = "override"
                assumption = (
                    f"overridden by {override.author}: {override.reason} "
                    f"(engine computed {Money.sum((d.amount for d in drafts), currency)})"
                )
                override_reason = override.reason

            cells.append(
                ForecastCell(
                    category=category,
                    week_index=index,
                    week_start=week_start,
                    week_end=week_start + timedelta(days=6),
                    amount=amount,
                    source=source,
                    assumption=assumption,
                    method=method,
                    contributions=contributions,
                    override_reason=override_reason,
                )
            )

    closing: list[Money] = []
    running = inputs.opening_cash
    for index in range(1, inputs.horizon_weeks + 1):
        running = running + Money.sum(
            (cell.amount for cell in cells if cell.week_index == index), currency
        )
        closing.append(running)

    # Undrawn capacity is committed and does not amortise inside the horizon, so
    # it adds a constant to every week's available liquidity.
    available = [balance + inputs.undrawn_revolver for balance in closing]

    return Forecast(
        version_id=inputs.version_id,
        as_of=inputs.as_of,
        currency=currency.code,
        opening_cash=inputs.opening_cash,
        weeks=weeks,
        cells=tuple(cells),
        closing_cash_by_week=tuple(closing),
        available_liquidity_by_week=tuple(available),
        undrawn_revolver=inputs.undrawn_revolver,
    )


def _summarise_assumptions(drafts: Sequence[_Draft]) -> str:
    """One assumption string per cell, without repeating identical text."""
    seen: list[str] = []
    for draft in drafts:
        if draft.assumption not in seen:
            seen.append(draft.assumption)
    if len(seen) == 1:
        return seen[0]
    head = "; ".join(seen[:2])
    if len(seen) > 2:
        return f"{head}; and {len(seen) - 2} further item(s)"
    return head


def roll_forward(
    inputs: ForecastInputs,
    *,
    version_id: str,
    as_of: datetime,
    opening_cash: Money,
) -> ForecastInputs:
    """Drop week 1, add week 14 (`WORKFLOW.md` §3).

    Overrides are *not* carried forward. An override is a judgement about a
    specific week made against specific evidence; silently re-applying it next
    cycle turns a one-off correction into an unreviewed permanent assumption,
    which is exactly the `stale_forecast_assumption` failure mode.
    """
    return replace(
        inputs,
        version_id=version_id,
        as_of=as_of,
        opening_cash=opening_cash,
        first_week_start=inputs.first_week_start + timedelta(weeks=1),
        overrides=(),
    )


def category_totals(forecast: Forecast) -> dict[str, Money]:
    return {category: forecast.category_total(category) for category in CATEGORIES}


def inflow_outflow(forecast: Forecast) -> tuple[Money, Money]:
    """`(total inflows, total outflows)` — outflows returned as a negative."""
    inflows = Money.sum(
        (cell.amount for cell in forecast.cells if cell.category in INFLOW_CATEGORIES),
        forecast.currency,
    )
    outflows = Money.sum(
        (cell.amount for cell in forecast.cells if cell.category not in INFLOW_CATEGORIES),
        forecast.currency,
    )
    return inflows, outflows


def render_grid(forecast: Forecast, *, weeks: int | None = None) -> str:
    """A plain-text 13-week grid, for golden files and the demo transcript."""
    shown = weeks or len(forecast.weeks)
    header = "category".ljust(24) + "".join(
        week.strftime(" %m-%d").rjust(12) for week in forecast.weeks[:shown]
    )
    lines = [header, "-" * len(header)]
    for category in CATEGORIES:
        row = category.ljust(24)
        for index in range(1, shown + 1):
            row += forecast.cell(category, index).amount.to_major_string().rjust(12)
        lines.append(row)
    lines.append("-" * len(header))
    closing = "closing cash".ljust(24)
    for value in forecast.closing_cash_by_week[:shown]:
        closing += value.to_major_string().rjust(12)
    lines.append(closing)
    liquidity = "available liquidity".ljust(24)
    for value in forecast.available_liquidity_by_week[:shown]:
        liquidity += value.to_major_string().rjust(12)
    lines.append(liquidity)
    return "\n".join(lines)


__all__ = [
    "AGING_BUCKETS",
    "BPS",
    "CATEGORIES",
    "HORIZON_WEEKS",
    "INFLOW_CATEGORIES",
    "CollectionCurve",
    "Contribution",
    "DatedAmount",
    "DebtServicePlan",
    "Forecast",
    "ForecastCell",
    "ForecastInputs",
    "ForecastOverride",
    "LagComponent",
    "OpenInvoice",
    "OpenPayable",
    "PayrollPlan",
    "SubscriptionBilling",
    "aging_bucket",
    "build",
    "category_totals",
    "inflow_outflow",
    "render_grid",
    "roll_forward",
]
