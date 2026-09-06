"""The deterministic NovaTech generator.

`make seed SEED=42` twice produces byte-identical rows. Three things make that
true rather than aspirational:

* every primary key is a `derived_id` over a natural key, so nothing depends on
  insertion order or on a random UUID;
* randomness comes from named streams (`rng.py`), so adding a generator step
  does not shift every other draw;
* every parameter is *fitted*, loaded from `calibration/novatech_profile.json`.
  Randomness samples from those distributions; it never invents a number.

**Reconciliation is structural, not checked afterwards.** Business facts are
generated first, and every posting to the general ledger is derived mechanically
from a fact by one of the rules in `_POSTINGS`. AR cannot drift from its control
account because the only thing that moves either is an invoice or an
application; the same holds for AP and for cash. The invariant gate in
`backend.models.invariants` then re-checks it, but it is verifying a property the
construction already guarantees rather than propping one up.

**Cadence comes from `finance/cadence.py`** — the same module the forecast uses.
Actuals and forecasts therefore share one calendar, which is what makes the
variance bridge meaningful instead of an artefact of two different date
generators.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlmodel import Session, select

from backend.finance import cadence
from backend.finance.cadence import BusinessCalendar, Convention, PayrollFrequency
from backend.finance.forecast import (
    CATEGORIES,
    DatedAmount,
    DebtServicePlan,
    ForecastInputs,
    OpenInvoice,
    OpenPayable,
    PayrollPlan,
    SubscriptionBilling,
)
from backend.finance.forecast import (
    build as build_forecast,
)
from backend.finance.money import Money
from backend.models import (
    AccountingPeriod,
    Assumption,
    BankAccount,
    BankHoliday,
    BankReconciliation,
    BankReconciliationItem,
    BankTransaction,
    Company,
    Customer,
    DebtCovenant,
    DebtFacility,
    EventLog,
    ForecastLine,
    ForecastVersion,
    GLAccount,
    GLTransaction,
    Invoice,
    JournalEntry,
    Payment,
    PaymentApplication,
    PaymentRun,
    Subscription,
    Vendor,
    VendorInvoice,
    assert_balanced,
    derived_id,
)
from backend.models.calendar import CalendarEvent
from backend.models.invariants import assert_consistent
from backend.models.policy import TreasuryPolicyRow
from backend.models.workflow import ApprovalRoute
from backend.seed.profile import Profile, load_profile
from backend.seed.rng import Streams, pareto_weights_bps

BPS = 10_000
DEFAULT_SEED = 42
DEFAULT_TENANT = "novatech"

#: Chart of accounts: (number, name, canonical role, normal balance is debit).
CHART_OF_ACCOUNTS: tuple[tuple[str, str, str, bool], ...] = (
    ("1000", "Cash - Operating", "cash_operating", True),
    ("1010", "Cash - Money Market", "cash_mmf", True),
    ("1020", "Cash - LC Collateral", "cash_restricted", True),
    ("1030", "Cash - Payroll", "cash_operating", True),
    ("1200", "Accounts Receivable - Trade", "ar_trade", True),
    ("2000", "Accounts Payable - Trade", "ap_trade", False),
    ("2500", "Revolving Credit Facility", "revolver_drawn", False),
    ("2600", "Term Loan A", "term_debt", False),
    ("3000", "Opening Equity", "opening_equity", False),
    ("4000", "Revenue", "revenue", False),
    ("6000", "Operating Expense", "operating_expense", True),
    ("6100", "Interest Expense", "interest_expense", True),
)

#: Bank accounts: (ref, name, GL account number, restricted).
BANK_ACCOUNTS: tuple[tuple[str, str, str, bool], ...] = (
    ("op-4471", "Operating", "1000", False),
    ("mm-9920", "Money Market", "1010", False),
    ("lc-1188", "LC Collateral", "1020", True),
    ("pr-5512", "Payroll", "1030", False),
)

CUSTOMER_NAMES: tuple[str, ...] = (
    "Aurora Systems",
    "Beacon Logistics",
    "Cedarline Health",
    "Delta Freight",
    "Everline Media",
    "Fairmount Retail",
    "Granite Analytics",
    "Harborview Foods",
    "Ironwood Manufacturing",
    "Juniper Labs",
    "Keystone Utilities",
    "Lakeshore Bank",
    "Meridian Transport",
    "Northgate Energy",
    "Orchard Insurance",
    "Pinnacle Robotics",
    "Quarry Materials",
    "Riverstone Legal",
    "Summit Aerospace",
    "Tallgrass Agriculture",
    "Umbra Security",
    "Vantage Hospitality",
    "Westbrook Chemicals",
    "Yellowfin Marine",
    "Zephyr Telecom",
    "Alder Diagnostics",
    "Bluepeak Software",
    "Copperfield Mining",
    "Dunmore Textiles",
    "Elmridge Education",
    "Foxglove Pharma",
    "Glenview Realty",
    "Halcyon Travel",
    "Inkwell Publishing",
    "Jetstream Airlines",
    "Kingfisher Water",
    "Larkspur Cosmetics",
    "Millbrook Dairy",
    "Nightingale Care",
    "Oakhaven Furniture",
    "Palisade Ventures",
    "Quill Financial",
)

VENDOR_NAMES: tuple[str, ...] = (
    "Atlas Cloud Services",
    "Borealis Datacenter",
    "Crestline Facilities",
    "Dockside Shipping",
    "Eastvale Components",
    "Fenwick Legal",
    "Groveland Staffing",
    "Highpoint Insurance",
    "Ivory Print",
    "Jasper Security",
    "Kilnwood Packaging",
    "Lumen Networks",
    "Maplewood Catering",
    "Norwood Consulting",
    "Oakline Couriers",
    "Petra Analytics",
    "Quantum Instruments",
    "Ridgeway Maintenance",
    "Silverbrook Travel",
    "Thornhill Recruiting",
    "Underhill Cleaning",
    "Verdant Landscaping",
    "Wexford Audit",
    "Xenon Laboratories",
    "Yarrow Marketing",
    "Zinnia Design",
    "Ashford Telecom",
    "Bridgeport Utilities",
    "Cobalt Hardware",
    "Drakemoor Freight",
    "Ellwood Chemicals",
    "Fallowfield Storage",
    "Gatewood Software",
    "Hollybank Payroll Services",
)

#: The low-spend, high-criticality, single-source vendor. Its existence is the
#: reason the Supplier Risk agent has a real objection rather than a scripted
#: one: criticality does not correlate with spend (`DATA_SOURCES.md` §5).
SINGLE_SOURCE_VENDOR = "Quantum Instruments"

CRITICAL_VENDORS: tuple[str, ...] = (
    SINGLE_SOURCE_VENDOR,
    "Borealis Datacenter",
    "Lumen Networks",
    "Hollybank Payroll Services",
)

#: Vendors whose invoices are a protected payment class. A deferral touching one
#: fails the constraint gate from the policy row, not from a prompt.
PROTECTED_VENDORS: dict[str, str] = {"Hollybank Payroll Services": "payroll"}

APPROVAL_MATRIX: tuple[tuple[str, int, int | None, str], ...] = (
    # (action, min_amount_minor, max_amount_minor, approver) — half-open bands,
    # so an amount $10 over a band falls in exactly one higher band.
    ("collection_call", 0, None, "analyst"),
    ("early_pay_discount", 0, 25_000_000, "treasurer"),
    ("early_pay_discount", 25_000_000, None, "cfo"),
    ("ap_deferral", 0, 25_000_000, "treasurer"),
    ("ap_deferral", 25_000_000, 100_000_000, "cfo"),
    ("ap_deferral", 100_000_000, None, "board"),
    ("revolver_draw", 0, 200_000_000, "cfo"),
    ("revolver_draw", 200_000_000, None, "board"),
)


@dataclass(frozen=True, slots=True)
class SeedConfig:
    """Everything that makes one seed run different from another."""

    seed: int = DEFAULT_SEED
    tenant_id: str = DEFAULT_TENANT
    company_name: str = "NovaTech Inc."
    #: The Monday the current forecast cycle opens on.
    as_of: date = date(2026, 9, 7)
    #: Weeks of transaction history generated before `as_of`.
    history_weeks: int = 30
    is_synthetic: bool = True

    def __post_init__(self) -> None:
        if self.as_of.weekday() != cadence.MONDAY:
            raise ValueError(f"as_of {self.as_of} must be a Monday")
        if self.history_weeks < 1:
            raise ValueError("history_weeks must be positive")

    @property
    def history_start(self) -> date:
        return self.as_of - timedelta(weeks=self.history_weeks)

    @property
    def as_of_datetime(self) -> datetime:
        return datetime(self.as_of.year, self.as_of.month, self.as_of.day, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class SeedResult:
    """What a caller needs to know about the dataset it just created."""

    company_id: str
    tenant_id: str
    seed: int
    as_of: date
    currency: str
    calendar: BusinessCalendar
    profile: Profile
    row_counts: dict[str, int]
    #: Weekly settled cash by category, for the closed weeks. This is the
    #: `actuals` series the variance bridge and the accuracy panel measure
    #: against; it is derived from the bank transactions, not asserted alongside.
    weekly_actuals: dict[date, dict[str, Money]]
    opening_cash: Money
    bank_gl_account: dict[str, str]


@dataclass(slots=True)
class _Counter:
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, table: str, n: int = 1) -> None:
        self.counts[table] = self.counts.get(table, 0) + n


class _Generator:
    """Holds the run's state so the generation steps stay readable."""

    def __init__(self, session: Session, config: SeedConfig, profile: Profile) -> None:
        self.session = session
        self.config = config
        self.profile = profile
        self.currency = profile.currency
        self.streams = Streams(config.seed)
        self.counter = _Counter()

        self.company_id = derived_id("companies", config.tenant_id, config.company_name)
        self.log = EventLog(config.tenant_id, self.company_id, is_synthetic=config.is_synthetic)
        self.common = {"tenant_id": config.tenant_id, "is_synthetic": config.is_synthetic}
        self.stamp = config.as_of_datetime
        self.calendar = cadence.us_calendar(config.history_start.year - 1, config.as_of.year + 2)

        self.accounts: dict[str, str] = {}  # account number -> id
        self.bank_ids: dict[str, str] = {}  # account ref -> id
        self.bank_gl: dict[str, str] = {}  # account ref -> gl account number
        self.bank_balance: dict[str, int] = {}
        self.periods: dict[str, str] = {}  # 'YYYY-MM' -> id
        self.journal_seq = 0
        self.weekly_actuals: dict[date, dict[str, Money]] = {}

    # -- primitives ------------------------------------------------------ #

    def zero(self) -> Money:
        return Money.zero(self.currency)

    def money(self, minor: int) -> Money:
        return Money.of(minor, self.currency)

    def _src(self, table: str, pk: str) -> dict[str, str]:
        return {"source_system": "seed", "source_table": table, "source_pk": pk}

    def _period_for(self, day: date) -> str:
        return self.periods[f"{day.year:04d}-{day.month:02d}"]

    def post(
        self,
        entry_ref: str,
        txn_date: date,
        memo: str,
        legs: Sequence[tuple[str, int, int]],
    ) -> None:
        """Post one balanced journal entry. Legs are `(account_number, dr, cr)`.

        Every ledger movement in the seed goes through here, which is what makes
        the subledger ties structural: there is no other way to touch the GL.
        """
        entry_id = derived_id("journal_entries", self.company_id, entry_ref)
        entry = JournalEntry(
            id=entry_id,
            company_id=self.company_id,
            period_id=self._period_for(txn_date),
            entry_ref=entry_ref,
            txn_date=txn_date,
            currency=self.currency,
            memo=memo[:512],
            effective_at=self.stamp,
            recorded_at=self.stamp,
            **self.common,
            **self._src("journal_entries", entry_ref),
        )
        lines = [
            GLTransaction(
                id=derived_id("gl_transactions", entry_id, str(index)),
                entry_id=entry_id,
                account_id=self.accounts[number],
                txn_date=txn_date,
                debit_minor=debit,
                credit_minor=credit,
                currency=self.currency,
                memo=memo[:512],
                line_no=index,
                effective_at=self.stamp,
                recorded_at=self.stamp,
                **self.common,
                **self._src("gl_transactions", f"{entry_ref}-{index}"),
            )
            for index, (number, debit, credit) in enumerate(legs, start=1)
        ]
        assert_balanced(entry_ref, lines)
        self.session.add(entry)
        self.session.flush()
        for line in lines:
            self.session.add(line)
        self.counter.add("journal_entries")
        self.counter.add("gl_transactions", len(lines))
        self.session.add(
            self.log.emit(
                "journal_posted",
                "journal_entries",
                entry_ref,
                {"memo": memo, "legs": [[number, dr, cr] for number, dr, cr in legs]},
                self.stamp,
            )
        )

    def bank_move(
        self,
        account_ref: str,
        day: date,
        amount_minor: int,
        counterparty: str,
        description: str,
        category: str | None,
        *,
        pending: bool = False,
        settle_lag_days: int = 0,
    ) -> None:
        """Append a bank transaction and keep the account's balance in step.

        `value_date` is the settlement date and is never earlier than the
        booking date — the model enforces that, and the distinction is what makes
        the pending-versus-posted split representable at all.
        """
        pk = f"{account_ref}-{day.isoformat()}-{description}-{amount_minor}"
        value_date = (
            self.calendar.add_business_days(day, settle_lag_days) if settle_lag_days else day
        )
        self.session.add(
            BankTransaction(
                id=derived_id("bank_transactions", self.company_id, pk),
                account_id=self.bank_ids[account_ref],
                booking_date=day,
                value_date=value_date,
                amount_minor=amount_minor,
                currency=self.currency,
                pending=pending,
                counterparty=counterparty[:256],
                description=description[:512],
                category=category,
                effective_at=self.stamp,
                recorded_at=self.stamp,
                **self.common,
                **self._src("bank_transactions", pk),
            )
        )
        self.counter.add("bank_transactions")
        if not pending:
            # Only settled cash moves the balance; the invariant gate checks it.
            self.bank_balance[account_ref] += amount_minor

    def record_actual(self, day: date, category: str, amount_minor: int) -> None:
        """Accumulate settled cash by forecast category, per closed week."""
        if day >= self.config.as_of:
            return
        week = cadence.week_start(day)
        bucket = self.weekly_actuals.setdefault(week, {})
        existing = bucket.get(category, self.zero())
        bucket[category] = existing + self.money(amount_minor)

    def cash_out(
        self,
        *,
        account_ref: str,
        day: date,
        amount_minor: int,
        counterparty: str,
        description: str,
        category: str,
        entry_ref: str,
        legs: Sequence[tuple[str, int, int]],
    ) -> None:
        """A settled outflow: bank movement, ledger posting and actual, together."""
        self.bank_move(account_ref, day, -amount_minor, counterparty, description, category)
        self.post(entry_ref, day, description, legs)
        self.record_actual(day, category, -amount_minor)

    # -- structure ------------------------------------------------------- #

    def build_company(self) -> None:
        self.session.add(
            Company(
                id=self.company_id,
                name=self.config.company_name,
                legal_name=f"{self.config.company_name} (Delaware)",
                currency=self.currency,
                country="US",
                business_timezone="America/New_York",
                fiscal_year_end_month=12,
                **self.common,
            )
        )
        self.session.add(
            self.log.emit(
                "company_registered",
                "companies",
                self.config.company_name,
                {"currency": self.currency},
                self.stamp,
            )
        )
        self.counter.add("companies")
        self.session.flush()

    def build_periods(self) -> None:
        """Monthly periods. Everything before the current month is closed.

        The cut-off is the period end, so a closed period legitimately contains
        its own postings — which is what a closed period is — while anything
        dated past the cut-off is the finding the invariant gate looks for.
        """
        current = f"{self.config.as_of.year:04d}-{self.config.as_of.month:02d}"
        for first in cadence.month_starts(
            self.config.history_start, self.config.as_of + timedelta(weeks=60)
        ):
            name = f"{first.year:04d}-{first.month:02d}"
            end = cadence.add_months(first, 1) - timedelta(days=1)
            period_id = derived_id("accounting_periods", self.company_id, name)
            self.periods[name] = period_id
            closed = name < current
            self.session.add(
                AccountingPeriod(
                    id=period_id,
                    company_id=self.company_id,
                    name=name,
                    start_date=first,
                    end_date=end,
                    status="closed" if closed else "open",
                    cutoff=end,
                    **self.common,
                )
            )
            self.counter.add("accounting_periods")
        self.session.flush()

    def build_chart_of_accounts(self) -> None:
        for number, name, role, normal_debit in CHART_OF_ACCOUNTS:
            account_id = derived_id("gl_accounts", self.company_id, number)
            self.accounts[number] = account_id
            self.session.add(
                GLAccount(
                    id=account_id,
                    company_id=self.company_id,
                    account_number=number,
                    name=name,
                    role=role,
                    currency=self.currency,
                    normal_debit=normal_debit,
                    **self.common,
                    **self._src("gl_accounts", number),
                )
            )
            self.session.add(
                self.log.emit(
                    "gl_account_opened", "gl_accounts", number, {"role": role}, self.stamp
                )
            )
            self.counter.add("gl_accounts")
        self.session.flush()

    def build_bank_accounts(self) -> None:
        for ref, name, gl_number, restricted in BANK_ACCOUNTS:
            account_id = derived_id("bank_accounts", self.company_id, ref)
            self.bank_ids[ref] = account_id
            self.bank_gl[ref] = gl_number
            self.bank_balance[ref] = 0
            self.session.add(
                BankAccount(
                    id=account_id,
                    company_id=self.company_id,
                    gl_account_id=self.accounts[gl_number],
                    name=name,
                    account_ref=ref,
                    currency=self.currency,
                    restricted=restricted,
                    current_balance_minor=0,
                    effective_at=self.stamp,
                    recorded_at=self.stamp,
                    **self.common,
                    **self._src("bank_accounts", ref),
                )
            )
            self.session.add(
                self.log.emit(
                    "bank_account_opened",
                    "bank_accounts",
                    ref,
                    {"restricted": restricted},
                    self.stamp,
                )
            )
            self.counter.add("bank_accounts")
        self.session.flush()

    def build_bank_holidays(self) -> None:
        for holiday in sorted(self.calendar.holidays):
            self.session.add(
                BankHoliday(
                    id=derived_id("bank_holidays", "US", holiday.isoformat()),
                    country="US",
                    holiday_date=holiday,
                    name="US federal bank holiday",
                    settlement_closed=True,
                )
            )
            self.counter.add("bank_holidays")
        self.session.flush()

    def build_opening_balances(self) -> None:
        """Opening cash, as one balanced entry against equity.

        Opening AR and AP are deliberately *not* seeded as balances. They emerge
        from generated invoices, because a plugged opening balance is precisely
        the number that later refuses to tie to its subledger.
        """
        scale = self.profile.section("scale")
        opening = {
            "op-4471": scale["opening_operating_cash_minor"],
            "mm-9920": scale["opening_money_market_cash_minor"],
            "lc-1188": scale["opening_restricted_cash_minor"],
            "pr-5512": scale["opening_payroll_cash_minor"],
        }
        day = self.config.history_start
        legs: list[tuple[str, int, int]] = []
        for ref, amount in opening.items():
            self.bank_move(ref, day, amount, "Opening balance", f"opening balance {ref}", "other")
            legs.append((self.bank_gl[ref], amount, 0))
        legs.append(("3000", 0, sum(opening.values())))
        self.post("JE-OPEN-0001", day, "opening cash balances", legs)

    def build_policy_and_routes(self) -> None:
        """Persist the policy version and the delegation-of-authority matrix."""
        from backend.finance.policy import TreasuryPolicy

        policy = TreasuryPolicy.load()
        self.session.add(
            TreasuryPolicyRow(
                id=derived_id("treasury_policies", self.config.tenant_id, str(policy.version)),
                version=policy.version,
                effective_at=policy.effective_at,
                currency=policy.currency,
                min_unrestricted_cash_minor=policy.min_unrestricted_cash.amount,
                min_30d_liquidity_minor=policy.min_30d_liquidity.amount,
                max_revolver_utilization_bps=policy.max_revolver_utilization_bps,
                protected_payment_classes=",".join(policy.protected_payment_classes),
                materiality_absolute_minor=policy.materiality.absolute.amount,
                materiality_pct_opex_bps=policy.materiality.pct_of_monthly_opex_bps,
                body_yaml=policy.model_dump_json(),
                **self.common,
            )
        )
        self.counter.add("treasury_policies")
        for action, low, high, approver in APPROVAL_MATRIX:
            self.session.add(
                ApprovalRoute(
                    id=derived_id("approval_routes", self.company_id, action, str(low)),
                    company_id=self.company_id,
                    action=action,
                    min_amount_minor=low,
                    max_amount_minor=high,
                    currency=self.currency,
                    approver=approver,
                    **self.common,
                )
            )
            self.counter.add("approval_routes")
        self.session.flush()

    # -- counterparties --------------------------------------------------- #

    def build_customers(self) -> list[tuple[str, str, str]]:
        """Return `(customer_id, name, segment)`, weighted by fitted segment mix."""
        stream = self.streams("customers")
        weights = self.profile.segment_weights_bps()
        segment_options = tuple(weights.items())
        count = min(self.profile.count("ar", "customer_count"), len(CUSTOMER_NAMES))
        customers: list[tuple[str, str, str]] = []
        for name in CUSTOMER_NAMES[:count]:
            segment = stream.weighted(segment_options)
            customer_id = derived_id("customers", self.company_id, name)
            self.session.add(
                Customer(
                    id=customer_id,
                    company_id=self.company_id,
                    name=name,
                    external_id=None,
                    segment=segment,
                    **self.common,
                    **self._src("customers", name),
                )
            )
            self.session.add(
                self.log.emit(
                    "customer_registered", "customers", name, {"segment": segment}, self.stamp
                )
            )
            self.counter.add("customers")
            customers.append((customer_id, name, segment))
        self.session.flush()
        return customers

    def build_vendors(self) -> list[tuple[str, str, int, str, bool]]:
        """Return `(vendor_id, name, spend_weight_bps, criticality, single_source)`."""
        ap = self.profile.section("ap")
        count = min(int(ap["vendor_count"]), len(VENDOR_NAMES))
        weights = pareto_weights_bps(
            count, int(ap["top_vendor_count"]), int(ap["top_vendor_spend_share_bps"])
        )
        vendors: list[tuple[str, str, int, str, bool]] = []
        for name, weight in zip(VENDOR_NAMES[:count], weights, strict=True):
            single_source = name == SINGLE_SOURCE_VENDOR
            criticality = "critical" if name in CRITICAL_VENDORS else "standard"
            vendor_id = derived_id("vendors", self.company_id, name)
            self.session.add(
                Vendor(
                    id=vendor_id,
                    company_id=self.company_id,
                    name=name,
                    criticality=criticality,
                    single_source=single_source,
                    replacement_lead_time_days=(
                        int(ap["single_source_replacement_lead_time_days"])
                        if single_source
                        else None
                    ),
                    protected_class=PROTECTED_VENDORS.get(name),
                    **self.common,
                    **self._src("vendors", name),
                )
            )
            self.session.add(
                self.log.emit(
                    "vendor_registered",
                    "vendors",
                    name,
                    {"criticality": criticality, "single_source": single_source},
                    self.stamp,
                )
            )
            self.counter.add("vendors")
            vendors.append((vendor_id, name, weight, criticality, single_source))
        self.session.flush()
        return vendors

    def build_subscriptions(self, customers: Sequence[tuple[str, str, str]]) -> None:
        processor = self.profile.section("processor")
        stream = self.streams("subscriptions")
        count = int(processor["subscription_count"])
        annual_share = int(processor["annual_share_bps"])
        for index in range(count):
            customer_id, customer_name, _segment = customers[index % len(customers)]
            annual = stream.happens(annual_share)
            external_id = f"sub_{index:04d}"
            amount = self.money(
                int(processor["annual_subscription_minor"])
                if annual
                else int(processor["monthly_subscription_minor"])
            )
            amount = stream.jitter(amount, 1_500)
            # Anniversary dates spread across the month, as real billing is.
            day_of_month = stream.integer(1, 28)
            start = self.config.history_start + timedelta(days=stream.integer(0, 180))
            next_billing = date(self.config.as_of.year, self.config.as_of.month, day_of_month)
            if next_billing < self.config.as_of:
                next_billing = cadence.add_months(next_billing, 1)
            self.session.add(
                Subscription(
                    id=derived_id("subscriptions", self.company_id, external_id),
                    company_id=self.company_id,
                    customer_id=customer_id,
                    external_id=external_id,
                    amount_minor=amount.amount,
                    currency=self.currency,
                    interval="annual" if annual else "monthly",
                    started_on=start,
                    next_billing_date=next_billing,
                    status="active",
                    effective_at=self.stamp,
                    recorded_at=self.stamp,
                    **self.common,
                    **self._src("subscriptions", external_id),
                )
            )
            self.session.add(
                self.log.emit(
                    "subscription_started",
                    "subscriptions",
                    external_id,
                    {"customer": customer_name, "amount_minor": amount.amount},
                    self.stamp,
                )
            )
            self.counter.add("subscriptions")
        self.session.flush()

    def build_debt(self) -> None:
        debt = self.profile.section("debt")
        covenants = self.profile.covenants()
        revolver_id = derived_id("debt_facilities", self.company_id, "Revolving Credit Facility")
        term_id = derived_id("debt_facilities", self.company_id, "Term Loan A")
        maturity = date(self.config.as_of.year + 3, 6, 30)

        self.session.add(
            DebtFacility(
                id=revolver_id,
                company_id=self.company_id,
                name="Revolving Credit Facility",
                limit_minor=int(debt["revolver_limit_minor"]),
                drawn_minor=int(debt["revolver_drawn_minor"]),
                currency=self.currency,
                base_rate_name="SOFR",
                spread_bps=int(debt["revolver_spread_bps"]),
                commitment_fee_bps=int(debt["revolver_commitment_fee_bps"]),
                max_utilization_bps=int(debt["revolver_max_utilization_bps"]),
                maturity=maturity,
                effective_at=self.stamp,
                recorded_at=self.stamp,
                **self.common,
                **self._src("debt_facilities", "revolver"),
            )
        )
        self.session.add(
            DebtFacility(
                id=term_id,
                company_id=self.company_id,
                name="Term Loan A",
                limit_minor=int(debt["term_loan_minor"]),
                drawn_minor=int(debt["term_loan_minor"]),
                currency=self.currency,
                base_rate_name="SOFR",
                spread_bps=int(debt["term_spread_bps"]),
                commitment_fee_bps=0,
                max_utilization_bps=BPS,
                maturity=maturity,
                effective_at=self.stamp,
                recorded_at=self.stamp,
                **self.common,
                **self._src("debt_facilities", "term_loan_a"),
            )
        )
        self.counter.add("debt_facilities", 2)
        self.session.flush()

        # The drawn balances are ledger facts too, or leverage is unauditable.
        self.post(
            "JE-DEBT-0001",
            self.config.history_start,
            "opening debt balances",
            (
                ("6000", int(debt["revolver_drawn_minor"]) + int(debt["term_loan_minor"]), 0),
                ("2500", 0, int(debt["revolver_drawn_minor"])),
                ("2600", 0, int(debt["term_loan_minor"])),
            ),
        )

        next_test = _next_quarter_end(self.config.as_of)
        definitions = (
            (
                "Consolidated Leverage Ratio",
                "Consolidated Total Net Debt to Consolidated Adjusted EBITDA for the "
                "trailing four fiscal quarters, tested on the last day of each fiscal quarter",
                covenants["leverage_ceiling_bps"],
                None,
            ),
            (
                "Interest Coverage Ratio",
                "Consolidated Adjusted EBITDA to Consolidated Interest Expense for the "
                "trailing four fiscal quarters, tested on the last day of each fiscal quarter",
                covenants["interest_coverage_floor_bps"],
                None,
            ),
            (
                "Minimum Liquidity",
                "Unrestricted cash plus undrawn revolving commitments, tested on the last "
                "day of each fiscal quarter",
                None,
                covenants["min_liquidity_minor"],
            ),
        )
        for name, definition, threshold_bps, threshold_minor in definitions:
            self.session.add(
                DebtCovenant(
                    id=derived_id("debt_covenants", revolver_id, name),
                    facility_id=revolver_id,
                    name=name,
                    definition=definition,
                    threshold_bps=threshold_bps,
                    threshold_minor=threshold_minor,
                    currency=self.currency if threshold_minor is not None else None,
                    test_frequency="quarterly",
                    next_test_date=next_test,
                    **self.common,
                    **self._src("debt_covenants", name),
                )
            )
            self.counter.add("debt_covenants")
        self.session.flush()

    # -- operating history ------------------------------------------------ #

    def build_ar(self, customers: Sequence[tuple[str, str, str]]) -> None:
        """Generate issued invoices and the receipts that have settled by `as_of`."""
        ar = self.profile.section("ar")
        stream = self.streams("ar")
        sizes = self.profile.segment_invoice_minor()
        terms = self.profile.segment_terms_days()
        curve = self.profile.collection_curve()
        weekly_count = int(ar["weekly_invoice_count"])
        sequence = 0
        for week in cadence.week_starts(self.config.history_start, self.config.history_weeks):
            for index in range(weekly_count):
                sequence += 1
                customer_id, customer_name, segment = customers[
                    (sequence + stream.integer(0, len(customers) - 1)) % len(customers)
                ]
                issue = self.calendar.add_business_days(week, index % 5)
                if issue >= self.config.as_of:
                    continue
                invoice_ref = f"NT-AR-{sequence:05d}"
                invoice_id = derived_id("invoices", self.company_id, invoice_ref)
                amount = stream.jitter_int(sizes[segment], 2_000)
                due = issue + timedelta(days=terms[segment])
                disputed = stream.happens(int(ar["dispute_rate_bps"]))
                component = stream.weighted(
                    tuple((item, item.weight_bps) for item in curve.mixture_for(segment))
                )
                collection = self.calendar.adjust(
                    due + timedelta(days=component.lag_days), Convention.FOLLOWING
                )
                paid = collection < self.config.as_of
                self.session.add(
                    Invoice(
                        id=invoice_id,
                        company_id=self.company_id,
                        customer_id=customer_id,
                        invoice_ref=invoice_ref,
                        issued_date=issue,
                        due_date=due,
                        amount_minor=amount,
                        open_amount_minor=0 if paid else amount,
                        currency=self.currency,
                        status="paid" if paid else "open",
                        disputed=disputed,
                        effective_at=self.stamp,
                        recorded_at=self.stamp,
                        **self.common,
                        **self._src("invoices", invoice_ref),
                    )
                )
                self.session.add(
                    self.log.emit(
                        "invoice_issued",
                        "invoices",
                        invoice_ref,
                        {"amount_minor": amount, "segment": segment},
                        self.stamp,
                    )
                )
                self.counter.add("invoices")
                self.post(
                    f"JE-{invoice_ref}",
                    issue,
                    f"invoice {invoice_ref}",
                    (("1200", amount, 0), ("4000", 0, amount)),
                )
                if not paid:
                    continue
                payment_ref = f"PMT-{invoice_ref}"
                payment_id = derived_id("payments", self.company_id, payment_ref)
                self.session.add(
                    Payment(
                        id=payment_id,
                        company_id=self.company_id,
                        payment_ref=payment_ref,
                        amount_minor=amount,
                        currency=self.currency,
                        paid_date=collection,
                        method="ach",
                        channel="bank",
                        effective_at=self.stamp,
                        recorded_at=self.stamp,
                        **self.common,
                        **self._src("payments", payment_ref),
                    )
                )
                self.session.flush()
                self.session.add(
                    PaymentApplication(
                        id=derived_id("payment_applications", payment_id, invoice_id),
                        payment_id=payment_id,
                        invoice_id=invoice_id,
                        amount_minor=amount,
                        currency=self.currency,
                        applied_date=collection,
                        effective_at=self.stamp,
                        recorded_at=self.stamp,
                        **self.common,
                        **self._src("payment_applications", payment_ref),
                    )
                )
                self.bank_move(
                    "op-4471",
                    collection,
                    amount,
                    customer_name,
                    f"receipt {payment_ref}",
                    "receipts_trade_ar",
                )
                self.post(
                    f"JE-{payment_ref}",
                    collection,
                    f"cash receipt {payment_ref}",
                    (("1000", amount, 0), ("1200", 0, amount)),
                )
                self.record_actual(collection, "receipts_trade_ar", amount)
                self.counter.add("payments")
                self.counter.add("payment_applications")
        self.session.flush()

    def build_ap(self, vendors: Sequence[tuple[str, str, int, str, bool]]) -> None:
        """Generate vendor bills and settle due items in Thursday payment runs."""
        ap = self.profile.section("ap")
        stream = self.streams("ap")
        options = tuple((vendor, vendor[2]) for vendor in vendors)
        weekly_count = int(ap["weekly_vendor_invoice_count"])
        terms = int(ap["invoice_terms_days"])
        invoices_by_run: dict[date, list[tuple[VendorInvoice, str]]] = {}
        sequence = 0
        run_dates = cadence.ap_run_dates(
            self.config.history_start,
            self.config.as_of - timedelta(days=1),
            self.calendar,
        )
        for week in cadence.week_starts(self.config.history_start, self.config.history_weeks):
            for index in range(weekly_count):
                sequence += 1
                vendor_id, vendor_name, _weight, _criticality, _single = stream.weighted(options)
                issue = self.calendar.add_business_days(week, index % 5)
                if issue >= self.config.as_of:
                    continue
                ref = f"NT-AP-{sequence:05d}"
                amount = stream.jitter_int(int(ap["typical_invoice_minor"]), 4_000)
                due = issue + timedelta(days=terms)
                candidate = next((day for day in run_dates if day >= due), None)
                paid = candidate is not None
                invoice = VendorInvoice(
                    id=derived_id("vendor_invoices", self.company_id, ref),
                    company_id=self.company_id,
                    vendor_id=vendor_id,
                    invoice_ref=ref,
                    issued_date=issue,
                    due_date=due,
                    amount_minor=amount,
                    open_amount_minor=0 if paid else amount,
                    currency=self.currency,
                    status="paid" if paid else "open",
                    early_pay_discount_bps=int(ap["early_pay_discount_bps"]),
                    early_pay_days=int(ap["early_pay_days"]),
                    effective_at=self.stamp,
                    recorded_at=self.stamp,
                    **self.common,
                    **self._src("vendor_invoices", ref),
                )
                self.session.add(invoice)
                self.counter.add("vendor_invoices")
                self.post(
                    f"JE-{ref}",
                    issue,
                    f"vendor invoice {ref}",
                    (("6000", amount, 0), ("2000", 0, amount)),
                )
                if candidate is not None:
                    invoices_by_run.setdefault(candidate, []).append((invoice, vendor_name))
        self.session.flush()
        for run_day, items in sorted(invoices_by_run.items()):
            run_ref = f"APRUN-{run_day.isoformat()}"
            run_id = derived_id("payment_runs", self.company_id, run_ref)
            total = sum(item.amount_minor for item, _ in items)
            self.session.add(
                PaymentRun(
                    id=run_id,
                    company_id=self.company_id,
                    run_date=run_day,
                    amount_minor=total,
                    currency=self.currency,
                    status="settled",
                    effective_at=self.stamp,
                    recorded_at=self.stamp,
                    **self.common,
                    **self._src("payment_runs", run_ref),
                )
            )
            for invoice, _vendor_name in items:
                invoice.payment_run_id = run_id
                self.session.add(invoice)
            self.cash_out(
                account_ref="op-4471",
                day=run_day,
                amount_minor=total,
                counterparty="Approved vendor batch",
                description=run_ref,
                category="ap_trade",
                entry_ref=f"JE-{run_ref}",
                legs=(("2000", total, 0), ("1000", 0, total)),
            )
            self.counter.add("payment_runs")
        self.session.flush()

    def _calendar_event(
        self, kind: str, day: date, label: str, amount_minor: int, *, protected: bool = False
    ) -> None:
        self.session.add(
            CalendarEvent(
                id=derived_id("calendar_events", self.company_id, kind, day.isoformat(), label),
                company_id=self.company_id,
                kind=kind,
                event_date=day,
                label=label,
                amount_minor=amount_minor,
                currency=self.currency,
                protected=protected,
                **self.common,
            )
        )
        self.counter.add("calendar_events")

    def build_payroll(self) -> None:
        payroll = self.profile.section("payroll")
        pay_days = cadence.pay_dates(
            self.profile.payroll_frequency(),
            self.config.history_start,
            self.config.as_of - timedelta(days=1),
            self.calendar,
            anchor=self.config.history_start,
        )
        salaried = int(payroll["salaried_gross_per_period_minor"])
        hourly = int(payroll["hourly_gross_per_period_minor"])
        benefits = int(payroll["benefits_per_period_minor"])
        tax_bps = int(payroll["employer_tax_bps"]) + int(payroll["employee_withholding_bps"])
        for pay_day in pay_days:
            for category, amount, label in (
                ("payroll_salaried", salaried, "salaried payroll"),
                ("payroll_hourly", hourly, "hourly payroll"),
                ("payroll_taxes_benefits", benefits, "employee benefits"),
            ):
                self._calendar_event("payroll", pay_day, label, amount, protected=True)
                self.cash_out(
                    account_ref="pr-5512",
                    day=pay_day,
                    amount_minor=amount,
                    counterparty="Hollybank Payroll Services",
                    description=f"{label} {pay_day.isoformat()}",
                    category=category,
                    entry_ref=f"JE-{category}-{pay_day.isoformat()}",
                    legs=(("6000", amount, 0), ("1030", 0, amount)),
                )
            tax = (salaried + hourly) * tax_bps // BPS
            for deposit in cadence.payroll_tax_deposit_dates(
                (pay_day,), self.calendar, schedule=self.profile.deposit_schedule()
            ):
                if deposit >= self.config.as_of:
                    continue
                self._calendar_event("payroll_tax", deposit, "employment tax deposit", tax)
                self.cash_out(
                    account_ref="pr-5512",
                    day=deposit,
                    amount_minor=tax,
                    counterparty="US Treasury",
                    description=f"payroll tax {pay_day.isoformat()}",
                    category="payroll_taxes_benefits",
                    entry_ref=f"JE-payroll-tax-{pay_day.isoformat()}",
                    legs=(("6000", tax, 0), ("1030", 0, tax)),
                )
        self.session.flush()

    def build_fixed_costs(self) -> None:
        fixed = self.profile.section("fixed_costs")
        start, end = self.config.history_start, self.config.as_of - timedelta(days=1)

        def expense(day: date, amount: int, category: str, kind: str, label: str) -> None:
            self._calendar_event(kind, day, label, amount)
            self.cash_out(
                account_ref="op-4471",
                day=day,
                amount_minor=amount,
                counterparty=label,
                description=f"{label} {day.isoformat()}",
                category=category,
                entry_ref=f"JE-{category}-{day.isoformat()}",
                legs=(("6000", amount, 0), ("1000", 0, amount)),
            )

        for day in cadence.monthly_dates(
            start, end, self.calendar, day_of_month=int(fixed["rent_day_of_month"])
        ):
            expense(day, int(fixed["rent_per_month_minor"]), "rent_leases", "rent", "Rent")
        for day in cadence.quarterly_estimated_tax_dates(start, end, self.calendar):
            expense(
                day,
                int(fixed["estimated_tax_per_quarter_minor"]),
                "tax_income_estimated",
                "tax_estimated",
                "Estimated income tax",
            )
        for day in cadence.monthly_dates(
            start, end, self.calendar, day_of_month=int(fixed["sales_tax_day_of_month"])
        ):
            expense(
                day,
                int(fixed["sales_tax_per_month_minor"]),
                "tax_sales_vat",
                "tax_sales",
                "Sales tax",
            )
        for year in range(start.year, end.year + 1):
            raw = date(year, int(fixed["insurance_month"]), int(fixed["insurance_day"]))
            day = self.calendar.adjust(raw, Convention.FOLLOWING)
            if start <= day <= end:
                expense(
                    day,
                    int(fixed["insurance_annual_minor"]),
                    "insurance_software",
                    "other",
                    "Annual insurance",
                )
        for quarter_end in (
            date(year, month, 1)
            for year in range(start.year, end.year + 1)
            for month in (3, 6, 9, 12)
        ):
            raw = cadence.add_months(quarter_end, 1) - timedelta(days=1)
            day = self.calendar.adjust(raw, Convention.MODIFIED_FOLLOWING)
            if start <= day <= end:
                expense(day, int(fixed["capex_per_quarter_minor"]), "capex", "other", "Capex")

        debt = self.profile.section("debt")
        service_days = cadence.debt_service_dates(
            date(start.year, 3, 31),
            start,
            end,
            self.calendar,
            frequency=self.profile.debt_service_frequency(),
        )
        drawn = int(debt["revolver_drawn_minor"]) + int(debt["term_loan_minor"])
        rate_bps = int(debt["base_rate_bps"]) + int(debt["term_spread_bps"])
        interest = drawn * rate_bps * 90 // (BPS * 360)
        principal = int(debt["term_loan_amortisation_per_quarter_minor"])
        for day in service_days:
            self._calendar_event("debt_service", day, "Debt interest", interest)
            self.cash_out(
                account_ref="op-4471",
                day=day,
                amount_minor=interest,
                counterparty="Debt syndicate",
                description=f"debt interest {day.isoformat()}",
                category="debt_interest",
                entry_ref=f"JE-debt-interest-{day.isoformat()}",
                legs=(("6100", interest, 0), ("1000", 0, interest)),
            )
            self._calendar_event("debt_service", day, "Term loan principal", principal)
            self.cash_out(
                account_ref="op-4471",
                day=day,
                amount_minor=principal,
                counterparty="Debt syndicate",
                description=f"term principal {day.isoformat()}",
                category="debt_principal",
                entry_ref=f"JE-debt-principal-{day.isoformat()}",
                legs=(("2600", principal, 0), ("1000", 0, principal)),
            )
        self.session.flush()

    def build_processor_receipts(self) -> None:
        processor = self.profile.section("processor")
        stream = self.streams("processor_receipts")
        subscriptions = self.session.exec(
            select(Subscription).where(Subscription.company_id == self.company_id)
        ).all()
        success_rate = int(processor["baseline_success_rate_bps"])
        lag = int(processor["payout_lag_business_days"])
        for subscription in subscriptions:
            interval_months = 12 if subscription.interval == "annual" else 1
            billing = date(
                self.config.history_start.year,
                self.config.history_start.month,
                subscription.next_billing_date.day,
            )
            while billing < self.config.history_start:
                billing = cadence.add_months(billing, interval_months)
            while billing < self.config.as_of:
                if stream.happens(success_rate):
                    payout = cadence.settlement_date(billing, self.calendar, lag_business_days=lag)
                    if payout < self.config.as_of:
                        ref = f"DODO-{subscription.external_id}-{billing.isoformat()}"
                        payment_id = derived_id("payments", self.company_id, ref)
                        amount = subscription.amount_minor
                        self.session.add(
                            Payment(
                                id=payment_id,
                                company_id=self.company_id,
                                subscription_id=subscription.id,
                                payment_ref=ref,
                                amount_minor=amount,
                                currency=self.currency,
                                paid_date=billing,
                                method="card",
                                channel="dodo",
                                effective_at=self.stamp,
                                recorded_at=self.stamp,
                                **self.common,
                                **self._src("payments", ref),
                            )
                        )
                        self.bank_move(
                            "op-4471",
                            payout,
                            amount,
                            "Dodo Payments",
                            f"processor payout {ref}",
                            "receipts_subscription",
                        )
                        self.post(
                            f"JE-{ref}",
                            payout,
                            f"processor payout {ref}",
                            (("1000", amount, 0), ("4000", 0, amount)),
                        )
                        self.record_actual(payout, "receipts_subscription", amount)
                        self.counter.add("payments")
                billing = cadence.add_months(billing, interval_months)
        self.session.flush()

    def finalize_bank_balances(self) -> None:
        for ref, account_id in self.bank_ids.items():
            account = self.session.get(BankAccount, account_id)
            if account is None:  # pragma: no cover - construction guarantees it
                raise AssertionError(f"missing bank account {ref}")
            account.current_balance_minor = self.bank_balance[ref]
            self.session.add(account)
        self.session.flush()

    def build_operating_recon(self) -> None:
        bank = self.profile.section("bank")
        balance = self.bank_balance["op-4471"]
        outstanding = int(bank["outstanding_cheque_minor"])
        deposits = int(bank["deposit_in_transit_minor"])
        fees = int(bank["unrecorded_fee_minor"])
        book_adjustments = -outstanding + deposits + fees
        adjusted = balance - outstanding + deposits
        recon_id = derived_id(
            "bank_reconciliations", self.company_id, self.config.as_of.isoformat()
        )
        self.session.add(
            BankReconciliation(
                id=recon_id,
                account_id=self.bank_ids["op-4471"],
                as_of=self.config.as_of,
                balance_per_bank_minor=balance,
                outstanding_cheques_minor=outstanding,
                deposits_in_transit_minor=deposits,
                unrecorded_bank_fees_minor=fees,
                adjusted_bank_minor=adjusted,
                balance_per_gl_minor=balance,
                book_adjustments_minor=book_adjustments,
                adjusted_book_minor=adjusted,
                difference_minor=0,
                currency=self.currency,
                prepared_by="controller",
                reviewed_by="treasurer",
                approved_by="cfo",
                effective_at=self.stamp,
                recorded_at=self.stamp,
                **self.common,
                **self._src("bank_reconciliations", self.config.as_of.isoformat()),
            )
        )
        for kind, description, amount, age in (
            ("outstanding_cheque", "Uncleared supplier cheques", -outstanding, 12),
            ("deposit_in_transit", "Processor deposits in transit", deposits, 2),
            ("unrecorded_fee", "Bank service fees", -fees, int(bank["aged_exception_days"])),
        ):
            self.session.add(
                BankReconciliationItem(
                    id=derived_id("bank_reconciliation_items", recon_id, kind),
                    reconciliation_id=recon_id,
                    kind=kind,
                    description=description,
                    amount_minor=amount,
                    currency=self.currency,
                    age_days=age,
                    resolved=False,
                    **self.common,
                )
            )
            self.counter.add("bank_reconciliation_items")
        self.counter.add("bank_reconciliations")
        self.session.flush()

    def _forecast_inputs(self, version_id: str, as_of_day: date) -> ForecastInputs:
        ar_rows = self.session.exec(
            select(Invoice, Customer)
            .join(Customer, Customer.id == Invoice.customer_id)
            .where(Invoice.company_id == self.company_id, Invoice.issued_date <= as_of_day)
        ).all()
        open_invoices = tuple(
            OpenInvoice(
                invoice_ref=invoice.invoice_ref,
                customer=customer.name,
                open_amount=self.money(invoice.open_amount_minor),
                due_date=invoice.due_date,
                segment=customer.segment,
                disputed=invoice.disputed,
                promise_to_pay=invoice.promise_to_pay,
            )
            for invoice, customer in ar_rows
            if invoice.open_amount_minor > 0
        )
        ap_rows = self.session.exec(
            select(VendorInvoice, Vendor)
            .join(Vendor, Vendor.id == VendorInvoice.vendor_id)
            .where(
                VendorInvoice.company_id == self.company_id,
                VendorInvoice.issued_date <= as_of_day,
                VendorInvoice.open_amount_minor > 0,
            )
        ).all()
        open_payables = tuple(
            OpenPayable(
                invoice_ref=invoice.invoice_ref,
                vendor=vendor.name,
                open_amount=self.money(invoice.open_amount_minor),
                due_date=invoice.due_date,
                criticality=vendor.criticality,
                single_source=vendor.single_source,
                replacement_lead_time_days=vendor.replacement_lead_time_days,
                early_pay_discount_bps=invoice.early_pay_discount_bps,
                early_pay_days=invoice.early_pay_days,
            )
            for invoice, vendor in ap_rows
        )
        subscriptions = tuple(
            SubscriptionBilling(
                subscription_ref=row.external_id,
                customer=row.external_id,
                amount=self.money(row.amount_minor),
                next_billing_date=row.next_billing_date,
                interval=cadence.ServiceFrequency(row.interval),
                status=row.status,
            )
            for row in self.session.exec(
                select(Subscription).where(Subscription.company_id == self.company_id)
            ).all()
        )
        opening = sum(
            transaction.amount_minor
            for transaction in self.session.exec(
                select(BankTransaction)
                .join(BankAccount, BankAccount.id == BankTransaction.account_id)
                .where(
                    BankAccount.company_id == self.company_id,
                    BankTransaction.value_date < as_of_day,
                    BankTransaction.pending.is_(False),
                )
            ).all()
        )
        payroll = self.profile.section("payroll")
        fixed = self.profile.section("fixed_costs")
        debt = self.profile.section("debt")
        sales_tax = tuple(
            DatedAmount(
                reference=f"sales-tax-{day.isoformat()}",
                amount=self.money(-int(fixed["sales_tax_per_month_minor"])),
                due_date=day,
                description="monthly sales tax",
            )
            for day in cadence.monthly_dates(
                as_of_day,
                as_of_day + timedelta(weeks=13),
                self.calendar,
                day_of_month=int(fixed["sales_tax_day_of_month"]),
            )
        )
        capex = tuple(
            DatedAmount(
                reference=f"capex-{day.isoformat()}",
                amount=self.money(-int(fixed["capex_per_quarter_minor"])),
                due_date=day,
                description="quarterly capex",
            )
            for day in cadence.debt_service_dates(
                date(as_of_day.year, 3, 31),
                as_of_day,
                as_of_day + timedelta(weeks=13),
                self.calendar,
            )
        )
        undrawn = int(debt["revolver_limit_minor"]) - int(debt["revolver_drawn_minor"])
        return ForecastInputs(
            version_id=version_id,
            as_of=datetime(as_of_day.year, as_of_day.month, as_of_day.day, tzinfo=UTC),
            first_week_start=as_of_day,
            opening_cash=self.money(opening),
            calendar=self.calendar,
            undrawn_revolver=self.money(undrawn),
            open_invoices=open_invoices,
            collection_curve=self.profile.collection_curve(),
            subscriptions=subscriptions,
            processor_success_rate_bps=int(
                self.profile.section("processor")["baseline_success_rate_bps"]
            ),
            processor_payout_lag_days=int(
                self.profile.section("processor")["payout_lag_business_days"]
            ),
            payroll=PayrollPlan(
                salaried_gross_per_period=self.money(
                    int(payroll["salaried_gross_per_period_minor"])
                ),
                hourly_gross_per_period=self.money(int(payroll["hourly_gross_per_period_minor"])),
                frequency=self.profile.payroll_frequency(),
                salaried_anchor=self.config.history_start,
                hourly_anchor=self.config.history_start,
                employer_tax_bps=int(payroll["employer_tax_bps"]),
                employee_withholding_bps=int(payroll["employee_withholding_bps"]),
                benefits_per_period=self.money(int(payroll["benefits_per_period_minor"])),
                deposit_schedule=self.profile.deposit_schedule(),
            ),
            open_payables=open_payables,
            rent_per_month=self.money(int(fixed["rent_per_month_minor"])),
            rent_day_of_month=int(fixed["rent_day_of_month"]),
            estimated_tax_per_quarter=self.money(int(fixed["estimated_tax_per_quarter_minor"])),
            sales_tax=sales_tax,
            debt_service=(
                DebtServicePlan(
                    facility_id="term-loan-a",
                    name="Term Loan A",
                    drawn=self.money(int(debt["term_loan_minor"])),
                    all_in_rate_bps=int(debt["base_rate_bps"]) + int(debt["term_spread_bps"]),
                    first_service_date=date(as_of_day.year, 3, 31),
                    frequency=self.profile.debt_service_frequency(),
                    principal_per_period=self.money(
                        int(debt["term_loan_amortisation_per_quarter_minor"])
                    ),
                ),
            ),
            capex=capex,
        )

    def build_forecast_history(self) -> None:
        history = self.profile.section("history")
        weeks = int(history["forecast_history_weeks"])
        errors = self.profile.category_error_bps()
        multipliers = self.profile.horizon_error_multiplier_bps()
        stream = self.streams("forecast_noise")

        def multiplier(horizon: int) -> int:
            eligible = [key for key in multipliers if key <= horizon]
            return multipliers[max(eligible)] if eligible else BPS

        for offset in range(weeks - 1, -1, -1):
            as_of_day = self.config.as_of - timedelta(weeks=offset)
            label = f"weekly-{as_of_day.isoformat()}"
            version_id = derived_id("forecast_versions", self.company_id, label)
            inputs = self._forecast_inputs(version_id, as_of_day)
            forecast = build_forecast(inputs)
            self.session.add(
                ForecastVersion(
                    id=version_id,
                    company_id=self.company_id,
                    version_label=label,
                    as_of=inputs.as_of,
                    data_recorded_through=inputs.as_of,
                    week_ending=as_of_day + timedelta(days=6),
                    first_week_start=as_of_day,
                    horizon_weeks=inputs.horizon_weeks,
                    published=True,
                    published_at=inputs.as_of,
                    published_by="treasury",
                    opening_cash_minor=inputs.opening_cash.amount,
                    currency=self.currency,
                    **self.common,
                )
            )
            self.session.flush()
            assumption_ids: dict[str, str] = {}
            for category in CATEGORIES:
                assumption_id = derived_id("assumptions", version_id, category)
                assumption_ids[category] = assumption_id
                self.session.add(
                    Assumption(
                        id=assumption_id,
                        version_id=version_id,
                        category=category,
                        key="baseline",
                        statement=f"Engine baseline for {category}",
                        basis="NovaTech fitted calibration profile",
                        as_of=as_of_day,
                        review_due=as_of_day + timedelta(days=14),
                        **self.common,
                    )
                )
                self.counter.add("assumptions")
            self.session.flush()
            for cell in forecast.cells:
                error = errors[cell.category] * multiplier(cell.week_index) // BPS
                direction = -1 if stream.happens(5_000) else 1
                factor = max(0, BPS + direction * error)
                amount = cell.amount.amount * factor // BPS
                source_pk = f"{label}-{cell.category}-{cell.week_index}"
                self.session.add(
                    ForecastLine(
                        id=derived_id("forecast_lines", version_id, source_pk),
                        version_id=version_id,
                        assumption_id=assumption_ids[cell.category],
                        category=cell.category,
                        week_index=cell.week_index,
                        week_start=cell.week_start,
                        week_end=cell.week_end,
                        amount_minor=amount,
                        currency=self.currency,
                        source=cell.source,
                        assumption=cell.assumption,
                        method=cell.method,
                        as_of=inputs.as_of,
                        **self.common,
                        **self._src("forecast_lines", source_pk),
                    )
                )
                self.counter.add("forecast_lines")
            self.counter.add("forecast_versions")
        self.session.flush()


def _next_quarter_end(day: date) -> date:
    """The first fiscal quarter end on or after `day`."""
    for month in (3, 6, 9, 12):
        candidate = cadence.add_months(date(day.year, month, 1), 1) - timedelta(days=1)
        if candidate >= day:
            return candidate
    return date(day.year + 1, 3, 31)


def seed(
    session: Session,
    config: SeedConfig | None = None,
    profile: Profile | None = None,
) -> SeedResult:
    """Build the complete deterministic NovaTech dataset and run its invariant gate."""
    chosen = config or SeedConfig()
    fitted = profile or load_profile()
    generator = _Generator(session, chosen, fitted)
    generator.build_company()
    generator.build_periods()
    generator.build_chart_of_accounts()
    generator.build_bank_accounts()
    generator.build_bank_holidays()
    generator.build_opening_balances()
    generator.build_policy_and_routes()
    customers = generator.build_customers()
    vendors = generator.build_vendors()
    generator.build_subscriptions(customers)
    generator.build_debt()
    generator.build_ar(customers)
    generator.build_ap(vendors)
    generator.build_payroll()
    generator.build_fixed_costs()
    generator.build_processor_receipts()
    generator.finalize_bank_balances()
    generator.build_operating_recon()
    generator.build_forecast_history()
    session.flush()
    assert_consistent(session)
    scale = fitted.section("scale")
    opening_cash = sum(
        int(scale[key])
        for key in (
            "opening_operating_cash_minor",
            "opening_money_market_cash_minor",
            "opening_restricted_cash_minor",
            "opening_payroll_cash_minor",
        )
    )
    return SeedResult(
        company_id=generator.company_id,
        tenant_id=chosen.tenant_id,
        seed=chosen.seed,
        as_of=chosen.as_of,
        currency=generator.currency,
        calendar=generator.calendar,
        profile=fitted,
        row_counts=dict(sorted(generator.counter.counts.items())),
        weekly_actuals={
            week: dict(sorted(categories.items()))
            for week, categories in sorted(generator.weekly_actuals.items())
        },
        opening_cash=generator.money(opening_cash),
        bank_gl_account=dict(generator.bank_gl),
    )


__all__ = [
    "APPROVAL_MATRIX",
    "BANK_ACCOUNTS",
    "CHART_OF_ACCOUNTS",
    "CRITICAL_VENDORS",
    "CUSTOMER_NAMES",
    "DEFAULT_SEED",
    "DEFAULT_TENANT",
    "PROTECTED_VENDORS",
    "SINGLE_SOURCE_VENDOR",
    "VENDOR_NAMES",
    "BusinessCalendar",
    "Convention",
    "PayrollFrequency",
    "SeedConfig",
    "SeedResult",
    "load_profile",
    "seed",
]
