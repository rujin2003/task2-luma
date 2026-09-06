"""The tool layer over a ledger the DB Agent has just loaded.

`EngineToolset` reads a fully modelled tenant: forecast versions, covenants, accuracy
history, Dodo subscriptions, a policy row. A tenant that arrived twenty seconds ago
through `/api/onboarding` has none of that. It has customers, vendors, invoices, vendor
invoices, bank accounts and bank movements — the six things a source database actually
gives you — and every screen in this product then has to be honest about the difference.

So this toolset does two things, and refuses to do a third:

* It **derives** what open receivables, open payables and settled cash movements can
  legitimately support: a direct-method thirteen-week forecast, an aging summary, ranked
  collection and deferral candidates, a supplier profile, the policy thresholds.
* It **declines** what they cannot: there is no variance bridge without a prior published
  version, no measured error percentiles without forecast history, no covenant status
  without facilities, and no Dodo breakdown without Dodo. Each of those raises `ToolError`
  with the reason, the agent records `degraded`, and the screen says which agent was
  working blind. That is the required behaviour, not a gap.

Nothing here invents a number. The modelling choices that are not in the tenant's data —
a collection curve, a deferral window — live in `config/tenant_terms.yaml`, and every
figure derived from one carries the band it came from in its own basis string.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func
from sqlmodel import Session, select

from backend.contracts.constraints import Constraint, ConstraintKind, ConstraintSeverity
from backend.contracts.money import Money
from backend.contracts.provenance import SourceSystem, parse_reference
from backend.finance.policy import TreasuryPolicy
from backend.models import (
    BankAccount,
    BankTransaction,
    Company,
    Customer,
    Invoice,
    Vendor,
    VendorInvoice,
)
from backend.tools.results import (
    AgingBucket,
    ArAgingSummary,
    AssumptionRow,
    CapabilityManifest,
    CollectionOpportunities,
    CollectionRow,
    CovenantStatus,
    DeferralCandidates,
    DeferralRow,
    DodoDeclineBreakdown,
    DriverAssumptions,
    EvidenceRow,
    ForecastErrorPercentiles,
    ForecastSummary,
    ForecastWeekRow,
    LiquidityPosition,
    PolicyConstraints,
    SupplierRiskProfile,
    VarianceBridge,
    cap,
)
from backend.tools.toolset import DEFAULT_TOP_N, MAX_ROWS, ToolError

BPS = 10_000

TERMS_PATH = Path(__file__).resolve().parents[2] / "config" / "tenant_terms.yaml"

#: Aging bands, oldest last. The key is the band name used in `tenant_terms.yaml`, and
#: the bound is the inclusive upper edge of days past due (`None` is the open band).
AGING_BANDS: tuple[tuple[str, str, int | None], ...] = (
    ("not_yet_due", "Not yet due", 0),
    ("d1_30", "1-30 days", 30),
    ("d31_60", "31-60 days", 60),
    ("d61_90", "61-90 days", 90),
    ("d90_plus", "90+ days", None),
)


class TenantTerms:
    """The stated, versioned assumptions applied on top of a raw tenant ledger."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.version = int(raw["version"])
        self.collection_probability_bps: dict[str, int] = {
            str(key): int(value) for key, value in raw["collection_probability_bps"].items()
        }
        self.disputed_multiplier_bps = int(raw["disputed_multiplier_bps"])
        self.max_delay_days_by_criticality: dict[str, int] = {
            str(key): int(value) for key, value in raw["max_delay_days_by_criticality"].items()
        }
        self.horizon_weeks = int(raw["horizon_weeks"])
        self.stale_after_days = int(raw["stale_after_days"])

    def probability_bps(self, band: str, *, disputed: bool) -> int:
        base = self.collection_probability_bps.get(band, 0)
        if disputed:
            return base * self.disputed_multiplier_bps // BPS
        return base

    def max_delay_days(self, criticality: str | None) -> int:
        key = (criticality or "unknown").strip().lower()
        return self.max_delay_days_by_criticality.get(
            key, self.max_delay_days_by_criticality.get("unknown", 0)
        )


@lru_cache(maxsize=1)
def load_terms(path: Path | None = None) -> TenantTerms:
    target = path or TERMS_PATH
    return TenantTerms(yaml.safe_load(target.read_text(encoding="utf-8")))


def band_of(days_past_due: int) -> tuple[str, str]:
    """`(band key, human label)` for a number of days past due."""
    for key, label, bound in AGING_BANDS:
        if bound is None or days_past_due <= bound:
            return key, label
    return AGING_BANDS[-1][0], AGING_BANDS[-1][1]


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


class TenantToolset:
    """Read-only tools over the rows one onboarding actually wrote.

    One SQLModel session is held for the life of the toolset. That is deliberate: the
    session is read-only, SQLite gives it a consistent view, and the alternative — a
    connection per tool call — would make an agent's six calls six different snapshots
    of a ledger that a second onboarding may be rewriting underneath it.
    """

    def __init__(
        self,
        session: Session,
        *,
        tenant_id: str,
        company_id: str | None = None,
        as_of: date | None = None,
        currency: str = "USD",
        policy: TreasuryPolicy | None = None,
        terms: TenantTerms | None = None,
    ) -> None:
        self.session = session
        self.tenant_id = tenant_id
        statement = select(Company).where(Company.tenant_id == tenant_id)
        if company_id is not None:
            statement = statement.where(Company.id == company_id)
        company = session.exec(statement).first()
        if company is None:
            raise ToolError(f"no company loaded for tenant {tenant_id!r}")
        self.company = company
        self.company_id = company.id
        self.currency = (company.currency or currency).upper()
        self.policy = policy or TreasuryPolicy.load()
        self.terms = terms or load_terms()
        self.as_of = as_of or self._latest_data_date()

    # --- shape of the loaded ledger ---------------------------------------------------

    def _latest_data_date(self) -> date:
        """The tenant's own clock. A March book must not be aged against a September wall."""
        candidates: list[date | None] = [
            self.session.exec(
                select(func.max(Invoice.issued_date)).where(Invoice.tenant_id == self.tenant_id)
            ).one(),
            self.session.exec(
                select(func.max(BankTransaction.booking_date)).where(
                    BankTransaction.tenant_id == self.tenant_id
                )
            ).one(),
            self.session.exec(
                select(func.max(VendorInvoice.issued_date)).where(
                    VendorInvoice.tenant_id == self.tenant_id
                )
            ).one(),
        ]
        dates = [value for value in candidates if isinstance(value, date)]
        if not dates:
            raise ToolError(
                f"tenant {self.tenant_id!r} has no dated rows; the load wrote no ledger to read"
            )
        return max(dates)

    def _money(self, minor: int) -> Money:
        return Money(minor_units=int(minor), currency=self.currency)

    def _open_invoices(self) -> list[Invoice]:
        return list(
            self.session.exec(
                select(Invoice)
                .where(Invoice.tenant_id == self.tenant_id, Invoice.open_amount_minor > 0)
                .order_by(Invoice.open_amount_minor.desc())
            ).all()
        )

    def _open_payables(self) -> list[VendorInvoice]:
        return list(
            self.session.exec(
                select(VendorInvoice)
                .where(
                    VendorInvoice.tenant_id == self.tenant_id,
                    VendorInvoice.open_amount_minor > 0,
                )
                .order_by(VendorInvoice.open_amount_minor.desc())
            ).all()
        )

    def _customers(self) -> dict[str, str]:
        return {
            row.id: row.name
            for row in self.session.exec(
                select(Customer).where(Customer.tenant_id == self.tenant_id)
            ).all()
        }

    def _vendors(self) -> dict[str, Vendor]:
        return {
            row.id: row
            for row in self.session.exec(
                select(Vendor).where(Vendor.tenant_id == self.tenant_id)
            ).all()
        }

    def _cash_today_minor(self) -> int:
        """Settled bank movements. Pending rows are excluded and reported separately."""
        total = self.session.exec(
            select(func.coalesce(func.sum(BankTransaction.amount_minor), 0)).where(
                BankTransaction.tenant_id == self.tenant_id,
                BankTransaction.pending == False,  # noqa: E712 -- SQL, not Python truthiness
            )
        ).one()
        return int(total)

    def _days_past_due(self, due: date) -> int:
        return (self.as_of - due).days

    # --- the derived direct-method forecast -------------------------------------------

    def _weeks(self) -> list[date]:
        first = _week_start(self.as_of) + timedelta(days=6)
        return [first + timedelta(weeks=index) for index in range(self.terms.horizon_weeks)]

    def _week_index(self, when: date, weeks: list[date]) -> int | None:
        """1-based week this date lands in; `None` if it falls outside the horizon.

        Anything already overdue lands in week 1 rather than being dropped — an overdue
        receivable is still expected, it is simply late, and dropping it would flatter
        the forecast.
        """
        if when <= weeks[0]:
            return 1
        for index, ending in enumerate(weeks, start=1):
            if when <= ending:
                return index
        return None

    def _forecast_rows(self) -> tuple[list[ForecastWeekRow], dict[str, Any]]:
        """The thirteen-week series, plus the components it was built from.

        Direct method, and only from rows the tenant actually gave us: expected receipts
        are open receivables at their due date weighted by the aging band's collection
        probability; expected disbursements are open payables at their due date in full.
        There is no run-rate, no seasonality and no revenue model, because none of those
        are in a source database and inventing one here would be the exact failure this
        product is supposed to prevent.
        """
        weeks = self._weeks()
        inflow: dict[int, int] = defaultdict(int)
        outflow: dict[int, int] = defaultdict(int)
        expected_total = 0

        for invoice in self._open_invoices():
            index = self._week_index(invoice.due_date, weeks)
            if index is None:
                continue
            band, _ = band_of(self._days_past_due(invoice.due_date))
            bps = self.terms.probability_bps(band, disputed=invoice.disputed)
            expected = invoice.open_amount_minor * bps // BPS
            inflow[index] += expected
            expected_total += expected

        for payable in self._open_payables():
            index = self._week_index(payable.due_date, weeks)
            if index is None:
                continue
            outflow[index] += payable.open_amount_minor

        opening = self._cash_today_minor()
        rows: list[ForecastWeekRow] = []
        floor_minor = self.policy.min_unrestricted_cash.amount
        balance = opening
        for index, ending in enumerate(weeks, start=1):
            balance = balance + inflow[index] - outflow[index]
            rows.append(
                ForecastWeekRow(
                    week_index=index,
                    week_ending=ending,
                    closing_cash=self._money(balance),
                    breaches_floor=balance < floor_minor,
                )
            )
        components = {
            "opening_minor": opening,
            "expected_receipts_minor": expected_total,
            "expected_disbursements_minor": sum(outflow.values()),
        }
        return rows, components

    @property
    def version_id(self) -> str:
        """Stable per tenant and as-of date, so two calls cite the same version."""
        return f"tenant-{self.tenant_id}-{self.as_of.isoformat()}"

    # --- shared position --------------------------------------------------------------

    async def get_liquidity_position(self) -> LiquidityPosition:
        rows, _ = self._forecast_rows()
        floor_minor = self.policy.min_unrestricted_cash.amount
        worst = min(rows, key=lambda row: row.closing_cash.minor_units)
        runway = self.terms.horizon_weeks
        for row in rows:
            if row.breaches_floor:
                runway = row.week_index - 1
                break
        return LiquidityPosition(
            cash_today=self._money(self._cash_today_minor()),
            floor=self._money(floor_minor),
            min_cash=worst.closing_cash,
            min_cash_week=worst.week_index,
            # No debt facility arrives through the DB Agent's six entities, so headroom is
            # reported as zero rather than assumed. `get_capability_manifest` says why.
            revolver_available=self._money(0),
            revolver_utilization_pct=Decimal("0"),
            runway_weeks=max(runway, 0),
            references=[f"forecast:{self.version_id}#closing_cash", "policy:min_unrestricted_cash"],
            as_of=self.as_of,
        )

    async def get_covenant_status(self) -> CovenantStatus:
        raise ToolError(
            "no debt facilities or covenants were loaded for this tenant; covenant headroom "
            "cannot be computed from receivables, payables and bank movements alone"
        )

    async def get_policy_constraints(self) -> PolicyConstraints:
        policy = self.policy
        constraints = [
            Constraint(
                constraint_id="min_unrestricted_cash",
                kind=ConstraintKind.MIN_CASH,
                severity=ConstraintSeverity.HARD,
                description="Unrestricted cash must not fall below the treasury floor.",
                money_threshold=self._money(policy.min_unrestricted_cash.amount),
                source_ref=f"policy:v{policy.version}#min_unrestricted_cash",
            ),
            Constraint(
                constraint_id="min_30d_liquidity",
                kind=ConstraintKind.MIN_30D_LIQUIDITY,
                severity=ConstraintSeverity.HARD,
                description="Projected 30-day liquidity must stay above the policy minimum.",
                money_threshold=self._money(policy.min_30d_liquidity.amount),
                source_ref=f"policy:v{policy.version}#min_30d_liquidity",
            ),
        ]
        constraints.extend(
            Constraint(
                constraint_id=f"protected_{name}",
                kind=ConstraintKind.PROTECTED_PAYMENT_CLASS,
                severity=ConstraintSeverity.HARD,
                description=f"{name.title()} payments may never be deferred.",
                applies_to=name,
                source_ref=f"policy:v{policy.version}#protected_payment_classes",
            )
            for name in policy.protected_payment_classes
        )
        return PolicyConstraints(
            constraints=constraints,
            references=[f"policy:v{policy.version}"],
            as_of=self.as_of,
        )

    async def get_capability_manifest(self) -> CapabilityManifest:
        """What this tenant actually has. The Commander plans and skips against this."""
        counts = self.counts()
        available: list[SourceSystem] = []
        missing: list[SourceSystem] = []
        notes: dict[str, str] = {}

        for source, count, note in (
            (SourceSystem.AR_LEDGER, counts["Invoice"], "open receivables loaded from the source"),
            (
                SourceSystem.AP_LEDGER,
                counts["VendorInvoice"],
                "open payables loaded from the source",
            ),
            (SourceSystem.BANK, counts["BankTransaction"], "settled bank movements loaded"),
        ):
            if count:
                available.append(source)
                notes[source.value] = f"{count:,} rows — {note}"
            else:
                missing.append(source)
                notes[source.value] = "the mapping produced no rows for this entity"

        available.append(SourceSystem.POLICY)
        notes[SourceSystem.POLICY.value] = (
            f"treasury policy v{self.policy.version} from configuration"
        )
        available.append(SourceSystem.FORECAST)
        notes[SourceSystem.FORECAST.value] = (
            "direct-method 13-week projection derived from the loaded ledger; "
            "no prior published version exists, so variance and measured error are unavailable"
        )

        for source, why in (
            (SourceSystem.DODO, "no Dodo payment events are connected for this tenant"),
            (SourceSystem.DEBT, "no debt facilities or covenants were in the source database"),
            (SourceSystem.GL, "the DB Agent maps sub-ledgers, not the general ledger"),
            (SourceSystem.PAYROLL, "payroll is not exposed by the source schema"),
            (SourceSystem.TAX_CALENDAR, "no tax calendar was in the source database"),
        ):
            missing.append(source)
            notes[source.value] = why

        return CapabilityManifest(
            available_sources=available,
            missing_sources=missing,
            notes=notes,
            as_of=self.as_of,
        )

    # --- forecast and variance --------------------------------------------------------

    async def get_forecast_summary(self, version_id: str | None = None) -> ForecastSummary:
        if version_id is not None and version_id != self.version_id:
            raise ToolError(
                f"this tenant has one derived version ({self.version_id}); "
                f"{version_id!r} was never published"
            )
        rows, _ = self._forecast_rows()
        return ForecastSummary(
            version_id=self.version_id,
            # Derived, never signed. A published version is a human act and no human has
            # published this one.
            published=False,
            weeks=rows,
            references=[f"forecast:{self.version_id}#closing_cash"],
            as_of=self.as_of,
        )

    async def list_driver_assumptions(
        self, *, top_n: int = DEFAULT_TOP_N, stale_only: bool = False
    ) -> DriverAssumptions:
        """The drivers this forecast rests on: two from the ledger, one from configuration."""
        _, components = self._forecast_rows()
        latest_ar = self.session.exec(
            select(func.max(Invoice.issued_date)).where(Invoice.tenant_id == self.tenant_id)
        ).one()
        latest_ap = self.session.exec(
            select(func.max(VendorInvoice.issued_date)).where(
                VendorInvoice.tenant_id == self.tenant_id
            )
        ).one()
        latest_bank = self.session.exec(
            select(func.max(BankTransaction.booking_date)).where(
                BankTransaction.tenant_id == self.tenant_id
            )
        ).one()

        candidates: list[tuple[str, str, str, date | None, str]] = [
            (
                "receipts",
                "Open receivables, probability-weighted by aging band",
                str(self._money(components["expected_receipts_minor"])),
                latest_ar if isinstance(latest_ar, date) else None,
                f"ar_ledger:{self.tenant_id}#open_amount_minor",
            ),
            (
                "disbursements",
                "Open payables at contractual due date",
                str(self._money(components["expected_disbursements_minor"])),
                latest_ap if isinstance(latest_ap, date) else None,
                f"ap_ledger:{self.tenant_id}#open_amount_minor",
            ),
            (
                "opening_cash",
                "Settled bank movements",
                str(self._money(components["opening_minor"])),
                latest_bank if isinstance(latest_bank, date) else None,
                f"bank:{self.tenant_id}#amount_minor",
            ),
            (
                "collection_curve",
                f"Collection probability by aging band (tenant_terms v{self.terms.version})",
                ", ".join(
                    f"{key}={value / 100:.0f}%"
                    for key, value in self.terms.collection_probability_bps.items()
                ),
                # A configured prior does not go stale with the ledger; it is dated to the
                # as-of so the row reads honestly rather than pretending to be a fact.
                self.as_of,
                f"policy:tenant_terms_v{self.terms.version}#collection_probability_bps",
            ),
        ]

        rows: list[AssumptionRow] = []
        for category, driver, display, refreshed, reference in candidates:
            when = refreshed or self.as_of
            age = max((self.as_of - when).days, 0)
            stale = age > self.terms.stale_after_days
            if stale_only and not stale:
                continue
            rows.append(
                AssumptionRow(
                    category=category,
                    driver=driver,
                    value_display=display,
                    last_refreshed=when,
                    days_since_refresh=age,
                    stale=stale,
                    reference=reference,
                )
            )
        shown, truncation = cap(rows, min(max(top_n, 1), MAX_ROWS))
        return DriverAssumptions(
            rows=shown,
            truncation=truncation,
            references=[row.reference for row in shown],
            as_of=self.as_of,
        )

    async def get_variance_bridge(
        self, *, week_ending: str | None = None, top_n: int = DEFAULT_TOP_N
    ) -> VarianceBridge:
        raise ToolError(
            "a variance bridge compares a published forecast with what actually happened, "
            "and this tenant has one week of loaded data and no prior published version"
        )

    async def get_forecast_error_percentiles(
        self, *, horizon_weeks: int
    ) -> ForecastErrorPercentiles:
        raise ToolError(
            "measured forecast error requires forecast history; this tenant has none, so a "
            "stress test must be calibrated from stated stressors rather than our own error"
        )

    # --- receivables ------------------------------------------------------------------

    async def rank_collection_opportunities(
        self, *, top_n: int = DEFAULT_TOP_N
    ) -> CollectionOpportunities:
        names = self._customers()
        invoices = self._open_invoices()
        rows: list[CollectionRow] = []
        total_open = 0
        total_expected = 0
        for invoice in invoices:
            days = self._days_past_due(invoice.due_date)
            band, label = band_of(days)
            bps = self.terms.probability_bps(band, disputed=invoice.disputed)
            expected = invoice.open_amount_minor * bps // BPS
            total_open += invoice.open_amount_minor
            total_expected += expected
            # Kept short deliberately: this string is repeated on every ranked row and a
            # model will echo it into each evidence excerpt, where a paragraph per row
            # costs more output budget than the finding is worth.
            basis = f"aging band {label}, {bps / 100:.0f}% (terms v{self.terms.version})"
            if invoice.disputed:
                basis = f"disputed; {basis}"
            rows.append(
                CollectionRow(
                    customer=names.get(invoice.customer_id, invoice.customer_id),
                    document_ref=invoice.invoice_ref,
                    amount=self._money(invoice.open_amount_minor),
                    due_date=invoice.due_date,
                    days_past_due=days,
                    probability_pct=Decimal(bps) / 100,
                    expected_amount=self._money(expected),
                    empirical_basis=basis,
                    reference=f"ar_ledger:{invoice.invoice_ref}#open_amount_minor",
                )
            )
        rows.sort(key=lambda row: row.expected_amount.minor_units, reverse=True)
        shown, truncation = cap(rows, min(max(top_n, 1), MAX_ROWS))
        return CollectionOpportunities(
            rows=shown,
            total_open=self._money(total_open),
            total_expected=self._money(total_expected),
            truncation=truncation,
            references=[row.reference for row in shown],
            as_of=self.as_of,
        )

    async def get_ar_aging_summary(self) -> ArAgingSummary:
        buckets: dict[str, tuple[int, int]] = {key: (0, 0) for key, _, _ in AGING_BANDS}
        total = 0
        for invoice in self._open_invoices():
            band, _ = band_of(self._days_past_due(invoice.due_date))
            amount, count = buckets[band]
            buckets[band] = (amount + invoice.open_amount_minor, count + 1)
            total += invoice.open_amount_minor
        return ArAgingSummary(
            buckets=[
                AgingBucket(
                    label=label,
                    amount=self._money(buckets[key][0]),
                    invoice_count=buckets[key][1],
                )
                for key, label, _ in AGING_BANDS
            ],
            total=self._money(total),
            references=[f"ar_ledger:{self.tenant_id}#aging"],
            as_of=self.as_of,
        )

    # --- payables and supplier risk ---------------------------------------------------

    async def rank_deferral_candidates(
        self, *, top_n: int = DEFAULT_TOP_N, max_delay_days: int = 30
    ) -> DeferralCandidates:
        vendors = self._vendors()
        rows: list[DeferralRow] = []
        deferrable = 0
        for payable in self._open_payables():
            vendor = vendors.get(payable.vendor_id)
            payment_class = (vendor.protected_class if vendor else None) or "trade"
            protected = self.policy.is_protected(payment_class)
            allowed = (
                0
                if protected
                else min(
                    self.terms.max_delay_days(vendor.criticality if vendor else None),
                    max_delay_days,
                )
            )
            if not protected:
                deferrable += payable.open_amount_minor
            discount = None
            if payable.early_pay_discount_bps:
                discount = self._money(payable.amount_minor * payable.early_pay_discount_bps // BPS)
            rows.append(
                DeferralRow(
                    supplier=vendor.name if vendor else payable.vendor_id,
                    document_ref=payable.invoice_ref,
                    amount=self._money(payable.open_amount_minor),
                    due_date=payable.due_date,
                    max_delay_days=allowed,
                    payment_class=payment_class,
                    protected=protected,
                    discount_forgone=discount,
                    reference=f"ap_ledger:{payable.invoice_ref}#open_amount_minor",
                )
            )
        # Protected rows are returned, marked, and sorted last: the AP agent has to be able
        # to see that payroll exists and is off limits.
        rows.sort(key=lambda row: (row.protected, -row.amount.minor_units))
        shown, truncation = cap(rows, min(max(top_n, 1), MAX_ROWS))
        return DeferralCandidates(
            rows=shown,
            total_deferrable=self._money(deferrable),
            truncation=truncation,
            references=[row.reference for row in shown],
            as_of=self.as_of,
        )

    async def get_supplier_risk_profile(self, *, supplier: str) -> SupplierRiskProfile:
        vendors = self._vendors()
        match = next(
            (row for row in vendors.values() if row.name.lower() == supplier.strip().lower()),
            None,
        ) or vendors.get(supplier)
        if match is None:
            raise ToolError(f"no vendor named {supplier!r} in this tenant's loaded ledger")

        payables = self._open_payables()
        total = sum(row.open_amount_minor for row in payables) or 1
        theirs = sum(row.open_amount_minor for row in payables if row.vendor_id == match.id)
        overdue = sum(
            1
            for row in payables
            if row.vendor_id == match.id and self._days_past_due(row.due_date) > 0
        )
        disputes = sum(
            1 for row in payables if row.vendor_id == match.id and row.status == "disputed"
        )
        terms_days = 0
        theirs_rows = [row for row in payables if row.vendor_id == match.id]
        if theirs_rows:
            terms_days = max(
                0,
                sum((row.due_date - row.issued_date).days for row in theirs_rows)
                // len(theirs_rows),
            )
        return SupplierRiskProfile(
            supplier=match.name,
            sole_source=match.single_source,
            concentration_pct=(Decimal(theirs) * 100 / Decimal(total)).quantize(Decimal("0.01")),
            # "Overdue right now", not "late over six months": the source database carries
            # one snapshot, and calling it a six-month history would be a fabrication.
            late_payments_6m=overdue,
            open_disputes=disputes,
            contractual_terms_days=terms_days,
            on_credit_hold=False,
            notes=(
                f"criticality {match.criticality!r} as carried by the source system; "
                f"{overdue} of this vendor's open invoices are already past due. "
                "Payment history beyond the current open book was not loaded."
            ),
            references=[f"ap_ledger:{match.name}#criticality"],
            as_of=self.as_of,
        )

    # --- dodo -------------------------------------------------------------------------

    async def get_dodo_decline_breakdown(
        self, *, top_n: int = DEFAULT_TOP_N
    ) -> DodoDeclineBreakdown:
        raise ToolError(
            "no Dodo payment events are connected for this tenant; collections at risk "
            "cannot be estimated without them"
        )

    # --- evidence ---------------------------------------------------------------------

    async def resolve_evidence(self, *, reference: str) -> EvidenceRow:
        try:
            source_name, record_id, field = parse_reference(reference)
            source = SourceSystem(source_name)
        except (ValueError, TypeError):
            return self._unresolved(reference, SourceSystem.FORECAST)

        if source is SourceSystem.POLICY:
            return self._resolve_policy(reference, record_id, field)
        if source is SourceSystem.FORECAST:
            return self._resolve_forecast(reference, record_id, field)

        model = {
            SourceSystem.AR_LEDGER: Invoice,
            SourceSystem.AP_LEDGER: VendorInvoice,
            SourceSystem.BANK: BankAccount,
        }.get(source)
        if model is None:
            return self._unresolved(reference, source)
        # References are spelled with the *tenant's own* key -- `ar_ledger:AR-4099`, not a
        # surrogate id. An analyst reading the Evidence Explorer sees the document number
        # that is on the invoice, and a model citing one is quoting something it can check.
        row: Any = self._by_business_key(model, record_id) or self.session.get(model, record_id)
        if row is None and source is SourceSystem.AP_LEDGER:
            row = self._by_name(Vendor, record_id) or self.session.get(Vendor, record_id)
        if row is None and source is SourceSystem.AR_LEDGER:
            row = self._by_name(Customer, record_id) or self.session.get(Customer, record_id)
        if row is None and source is SourceSystem.BANK:
            row = self.session.get(BankTransaction, record_id)
        if row is None or getattr(row, "tenant_id", None) != self.tenant_id:
            # An aggregate cites the tenant itself (`ar_ledger:<tenant>#aging`). That is a
            # real citation of a real derived total, so it resolves rather than breaking.
            if record_id == self.tenant_id:
                return EvidenceRow(
                    reference=reference,
                    source=source,
                    excerpt=f"{source.value} aggregate for {self.company.name}",
                    fields={"tenant_id": self.tenant_id, "as_of": self.as_of.isoformat()},
                    resolved=True,
                    references=[reference],
                    as_of=self.as_of,
                )
            return self._unresolved(reference, source)

        values = {
            key: str(value)
            for key, value in vars(row).items()
            if not key.startswith("_") and value is not None
        }
        if field is not None and field not in values:
            return EvidenceRow(
                reference=reference,
                source=source,
                excerpt="",
                fields=values,
                resolved=False,
                as_of=self.as_of,
            )
        label = getattr(row, "invoice_ref", None) or getattr(row, "name", None) or record_id
        return EvidenceRow(
            reference=reference,
            source=source,
            excerpt=f"{row.__class__.__name__} {label}",
            fields={field: values[field]} if field else values,
            resolved=True,
            references=[reference],
            as_of=self.as_of,
        )

    def _by_business_key(self, model: type, record_id: str) -> Any:
        column = {
            Invoice: Invoice.invoice_ref,
            VendorInvoice: VendorInvoice.invoice_ref,
            BankAccount: BankAccount.account_ref,
        }.get(model)
        if column is None:
            return None
        return self.session.exec(
            select(model).where(model.tenant_id == self.tenant_id, column == record_id)
        ).first()

    def _by_name(self, model: type, record_id: str) -> Any:
        return self.session.exec(
            select(model).where(model.tenant_id == self.tenant_id, model.name == record_id)
        ).first()

    def _resolve_policy(self, reference: str, record_id: str, field: str | None) -> EvidenceRow:
        dumped = self.policy.model_dump(mode="json")
        if field and field in dumped:
            return EvidenceRow(
                reference=reference,
                source=SourceSystem.POLICY,
                excerpt=f"treasury policy v{self.policy.version} · {field}",
                fields={field: str(dumped[field])},
                resolved=True,
                references=[reference],
                as_of=self.as_of,
            )
        if record_id.startswith("tenant_terms") or field in dumped or record_id.startswith("v"):
            return EvidenceRow(
                reference=reference,
                source=SourceSystem.POLICY,
                excerpt=f"treasury policy v{self.policy.version}",
                fields={key: str(value) for key, value in dumped.items()},
                resolved=True,
                references=[reference],
                as_of=self.as_of,
            )
        return self._unresolved(reference, SourceSystem.POLICY)

    def _resolve_forecast(self, reference: str, record_id: str, field: str | None) -> EvidenceRow:
        if record_id != self.version_id:
            return self._unresolved(reference, SourceSystem.FORECAST)
        rows, components = self._forecast_rows()
        return EvidenceRow(
            reference=reference,
            source=SourceSystem.FORECAST,
            excerpt=(
                f"derived 13-week direct forecast {self.version_id} "
                f"(unpublished; built from this tenant's open ledger)"
            ),
            fields={
                "opening_cash": str(self._money(components["opening_minor"])),
                "expected_receipts": str(self._money(components["expected_receipts_minor"])),
                "expected_disbursements": str(
                    self._money(components["expected_disbursements_minor"])
                ),
                "closing_week_13": str(rows[-1].closing_cash),
            },
            resolved=True,
            references=[reference],
            as_of=self.as_of,
        )

    def _unresolved(self, reference: str, source: SourceSystem) -> EvidenceRow:
        return EvidenceRow(
            reference=reference, source=source, excerpt="", resolved=False, as_of=self.as_of
        )

    def close(self) -> None:
        """Release the read session. Called when this toolset stops being the live one."""
        self.session.close()

    # --- what the screens ask for -----------------------------------------------------

    def counts(self) -> dict[str, int]:
        """Row counts by entity, for the data-source header and the manifest."""
        return {
            name: int(
                self.session.exec(select(func.count()).select_from(model).where(clause)).one()
            )
            for name, model, clause in (
                ("Customer", Customer, Customer.tenant_id == self.tenant_id),
                ("Vendor", Vendor, Vendor.tenant_id == self.tenant_id),
                ("BankAccount", BankAccount, BankAccount.tenant_id == self.tenant_id),
                ("Invoice", Invoice, Invoice.tenant_id == self.tenant_id),
                ("VendorInvoice", VendorInvoice, VendorInvoice.tenant_id == self.tenant_id),
                ("BankTransaction", BankTransaction, BankTransaction.tenant_id == self.tenant_id),
            )
        }


__all__ = ["AGING_BANDS", "TenantTerms", "TenantToolset", "band_of", "load_terms"]
