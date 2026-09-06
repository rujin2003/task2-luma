"""SQLModel-backed implementation of the agent tool interface."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from math import ceil

from sqlmodel import Session, select

from backend.contracts.constraints import Constraint, ConstraintKind, ConstraintSeverity
from backend.contracts.money import Money as ContractMoney
from backend.contracts.provenance import SourceSystem, parse_reference
from backend.finance import cash as cash_engine
from backend.finance import covenants as covenant_engine
from backend.finance.forecast import AGING_BUCKETS, CATEGORIES, aging_bucket
from backend.finance.money import Money as EngineMoney
from backend.models import (
    AccuracyStat,
    Assumption,
    BankAccount,
    BankTransaction,
    Company,
    Customer,
    DebtCovenant,
    DebtFacility,
    ForecastLine,
    ForecastVersion,
    GLAccount,
    Invoice,
    Subscription,
    TreasuryPolicyRow,
    VarianceItem,
    Vendor,
    VendorInvoice,
)
from backend.seed.profile import Profile, load_profile
from backend.tools.results import (
    AgingBucket,
    ArAgingSummary,
    AssumptionRow,
    CapabilityManifest,
    CollectionOpportunities,
    CollectionRow,
    CovenantRow,
    CovenantStatus,
    DeclineRow,
    DeferralCandidates,
    DeferralRow,
    DodoDeclineBreakdown,
    DriverAssumptions,
    ErrorPercentile,
    EvidenceRow,
    ForecastErrorPercentiles,
    ForecastSummary,
    ForecastWeekRow,
    LiquidityPosition,
    PolicyConstraints,
    SupplierRiskProfile,
    VarianceBridge,
    VarianceRow,
    cap,
)

BPS = 10_000
MAX_ROWS = 25
DEFAULT_TOP_N = 10


def _tool_error(message: str) -> Exception:
    from backend.tools.toolset import ToolError

    return ToolError(message)


def _money(amount_minor: int, currency: str) -> ContractMoney:
    """Translate the finance engine's minor-unit money to the agent contract."""
    return ContractMoney(minor_units=amount_minor, currency=currency)


def _limit(top_n: int) -> int:
    if top_n < 1:
        raise _tool_error("top_n must be at least 1")
    return min(top_n, MAX_ROWS)


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _nearest_rank(values: list[int], percentile: int) -> int:
    ordered = sorted(values)
    return ordered[max(0, ceil(percentile * len(ordered) / 100) - 1)]


class EngineToolset:
    """Read-only tools over a seeded or production-compatible SQLModel session."""

    def __init__(
        self,
        session: Session,
        *,
        tenant_id: str = "novatech",
        company_id: str | None = None,
        as_of: date | None = None,
    ) -> None:
        self.session = session
        self.tenant_id = tenant_id
        statement = select(Company).where(Company.tenant_id == tenant_id)
        if company_id is not None:
            statement = statement.where(Company.id == company_id)
        company = session.exec(statement).first()
        if company is None:
            suffix = f" and company {company_id!r}" if company_id else ""
            raise _tool_error(f"no company for tenant {tenant_id!r}{suffix}")
        self.company = company
        self.company_id = company.id
        self.currency = company.currency
        self.as_of = as_of or self._latest_data_date()
        self.profile: Profile = load_profile()

    def _latest_data_date(self) -> date:
        version = self.session.exec(
            select(ForecastVersion)
            .where(
                ForecastVersion.tenant_id == self.tenant_id,
                ForecastVersion.company_id == self.company_id,
            )
            .order_by(ForecastVersion.as_of.desc())
        ).first()
        return version.as_of.date() if version is not None else date.today()

    def _policy(self) -> TreasuryPolicyRow:
        policy = self.session.exec(
            select(TreasuryPolicyRow)
            .where(
                TreasuryPolicyRow.tenant_id == self.tenant_id,
                TreasuryPolicyRow.effective_at <= self.as_of,
            )
            .order_by(TreasuryPolicyRow.version.desc())
        ).first()
        if policy is None:
            raise _tool_error(f"no treasury policy for tenant {self.tenant_id!r}")
        return policy

    def _forecast_version(self, version_id: str | None = None) -> ForecastVersion:
        statement = select(ForecastVersion).where(
            ForecastVersion.tenant_id == self.tenant_id,
            ForecastVersion.company_id == self.company_id,
        )
        if version_id is not None:
            statement = statement.where(ForecastVersion.id == version_id)
        else:
            statement = statement.where(ForecastVersion.as_of <= self.as_of).order_by(
                ForecastVersion.as_of.desc()
            )
        version = self.session.exec(statement).first()
        if version is None:
            raise _tool_error(f"forecast version {version_id!r} was not found")
        return version

    def _accounts(self) -> list[BankAccount]:
        return list(
            self.session.exec(
                select(BankAccount).where(
                    BankAccount.tenant_id == self.tenant_id,
                    BankAccount.company_id == self.company_id,
                )
            ).all()
        )

    def _facilities(self) -> list[DebtFacility]:
        return list(
            self.session.exec(
                select(DebtFacility).where(
                    DebtFacility.tenant_id == self.tenant_id,
                    DebtFacility.company_id == self.company_id,
                )
            ).all()
        )

    def _safe_revolver_available(self, facilities: list[DebtFacility]) -> int:
        policy_limit = self._policy().max_revolver_utilization_bps
        return sum(
            max(
                0,
                facility.limit_minor * min(policy_limit, facility.max_utilization_bps) // BPS
                - facility.drawn_minor,
            )
            for facility in facilities
        )

    def _forecast_balances(
        self, version: ForecastVersion
    ) -> tuple[list[tuple[int, date, int]], list[ForecastLine]]:
        lines = list(
            self.session.exec(
                select(ForecastLine)
                .where(
                    ForecastLine.tenant_id == self.tenant_id,
                    ForecastLine.version_id == version.id,
                )
                .order_by(ForecastLine.week_index, ForecastLine.category)
            ).all()
        )
        net_by_week: dict[int, int] = defaultdict(int)
        ending_by_week: dict[int, date] = {}
        for line in lines:
            net_by_week[line.week_index] += line.amount_minor
            ending_by_week[line.week_index] = line.week_end
        running = version.opening_cash_minor
        balances: list[tuple[int, date, int]] = []
        for index in range(1, min(13, version.horizon_weeks) + 1):
            running += net_by_week[index]
            ending = ending_by_week.get(
                index, version.first_week_start + timedelta(weeks=index, days=-1)
            )
            balances.append((index, ending, running))
        return balances, lines

    async def get_liquidity_position(self) -> LiquidityPosition:
        accounts = self._accounts()
        if not accounts:
            raise _tool_error("liquidity position requires at least one bank account")
        account_ids = {account.id for account in accounts}
        pending = list(
            self.session.exec(
                select(BankTransaction).where(
                    BankTransaction.tenant_id == self.tenant_id,
                    BankTransaction.account_id.in_(account_ids),
                    BankTransaction.pending.is_(True),
                )
            ).all()
        )
        pending_by_account: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))
        for transaction in pending:
            incoming, outgoing = pending_by_account[transaction.account_id]
            if transaction.amount_minor > 0:
                incoming += transaction.amount_minor
            else:
                outgoing += abs(transaction.amount_minor)
            pending_by_account[transaction.account_id] = incoming, outgoing
        facilities = self._facilities()
        available_minor = self._safe_revolver_available(facilities)
        balances = tuple(
            cash_engine.AccountBalance(
                account_ref=account.account_ref,
                name=account.name,
                settled=EngineMoney.of(account.current_balance_minor, account.currency),
                restricted=account.restricted,
                pending_in=EngineMoney.of(pending_by_account[account.id][0], account.currency),
                pending_out=EngineMoney.of(pending_by_account[account.id][1], account.currency),
            )
            for account in accounts
        )
        position = cash_engine.aggregate(
            balances,
            undrawn_revolver=EngineMoney.of(available_minor, self.currency),
            as_of=self._forecast_version().as_of,
        )
        forecast_balances, _ = self._forecast_balances(self._forecast_version())
        if forecast_balances:
            min_week, _, min_cash = min(forecast_balances, key=lambda row: row[2])
            runway = next((index - 1 for index, _, value in forecast_balances if value <= 0), 13)
        else:
            min_week, min_cash, runway = 1, position.unrestricted_cash.amount, 0
        policy = self._policy()
        total_limit = sum(facility.limit_minor for facility in facilities)
        total_drawn = sum(facility.drawn_minor for facility in facilities)
        utilization = (
            Decimal(total_drawn * 100) / Decimal(total_limit) if total_limit else Decimal(0)
        )
        return LiquidityPosition(
            cash_today=_money(position.unrestricted_cash.amount, self.currency),
            floor=_money(policy.min_unrestricted_cash_minor, policy.currency),
            min_cash=_money(min_cash, self.currency),
            min_cash_week=min_week,
            revolver_available=_money(available_minor, self.currency),
            revolver_utilization_pct=utilization,
            runway_weeks=runway,
            references=[
                *(f"bank:{account.id}" for account in accounts),
                f"policy:{policy.id}",
            ],
            as_of=self.as_of,
        )

    async def get_covenant_status(self) -> CovenantStatus:
        facilities = self._facilities()
        facility_ids = {facility.id for facility in facilities}
        rows = list(
            self.session.exec(
                select(DebtCovenant).where(
                    DebtCovenant.tenant_id == self.tenant_id,
                    DebtCovenant.facility_id.in_(facility_ids),
                )
            ).all()
        )
        unrestricted = sum(
            account.current_balance_minor for account in self._accounts() if not account.restricted
        )
        total_debt = sum(facility.drawn_minor for facility in facilities)
        undrawn = self._safe_revolver_available(facilities)
        scale = self.profile.section("scale")
        debt_profile = self.profile.section("debt")
        rate_bps = int(debt_profile["base_rate_bps"]) + int(debt_profile["term_spread_bps"])
        interest_minor = max(1, total_debt * rate_bps // BPS)
        inputs = covenant_engine.CovenantInputs(
            unrestricted_cash=EngineMoney.of(unrestricted, self.currency),
            total_debt=EngineMoney.of(total_debt, self.currency),
            undrawn_revolver=EngineMoney.of(undrawn, self.currency),
            ttm_adjusted_ebitda=EngineMoney.of(
                int(scale["ttm_adjusted_ebitda_minor"]), self.currency
            ),
            ttm_interest_expense=EngineMoney.of(interest_minor, self.currency),
        )
        output: list[CovenantRow] = []
        for row in rows:
            lowered = f"{row.name} {row.definition}".lower()
            if row.threshold_minor is not None:
                kind = (
                    covenant_engine.CovenantKind.MIN_CASH
                    if "cash" in lowered and "liquidity" not in lowered
                    else covenant_engine.CovenantKind.MIN_LIQUIDITY
                )
            elif "interest" in lowered or "coverage" in lowered:
                kind = covenant_engine.CovenantKind.INTEREST_COVERAGE
            else:
                kind = covenant_engine.CovenantKind.NET_DEBT_TO_EBITDA
            definition = covenant_engine.CovenantDefinition(
                name=row.name,
                kind=kind,
                definition=row.definition,
                next_test_date=row.next_test_date,
                threshold_bps=row.threshold_bps,
                threshold_amount=(
                    EngineMoney.of(row.threshold_minor, row.currency or self.currency)
                    if row.threshold_minor is not None
                    else None
                ),
                test_frequency=covenant_engine.CovenantTestFrequency(row.test_frequency),
            )
            status = covenant_engine.evaluate(definition, inputs, self.as_of)
            if status.current_value_bps is not None and status.threshold_bps is not None:
                observed = Decimal(status.current_value_bps) / BPS
                threshold = Decimal(status.threshold_bps) / BPS
                headroom = Decimal(status.headroom_bps or 0) * 100 / status.threshold_bps
            else:
                threshold_minor = row.threshold_minor or 1
                actual_minor = (
                    inputs.unrestricted_cash.amount
                    if kind is covenant_engine.CovenantKind.MIN_CASH
                    else inputs.liquidity.amount
                )
                observed = Decimal(actual_minor) / threshold_minor
                threshold = Decimal(1)
                headroom = Decimal(actual_minor - threshold_minor) * 100 / threshold_minor
            reference = f"debt:{row.id}"
            output.append(
                CovenantRow(
                    covenant_id=row.id,
                    label=row.name,
                    observed_ratio=observed,
                    threshold_ratio=threshold,
                    headroom_pct=headroom,
                    breached=not status.passed,
                    tested_on=row.next_test_date,
                    reference=reference,
                )
            )
        return CovenantStatus(
            covenants=output,
            references=[item.reference for item in output],
            as_of=self.as_of,
        )

    async def get_policy_constraints(self) -> PolicyConstraints:
        policy = self._policy()
        reference = f"policy:{policy.id}"
        constraints = [
            Constraint(
                constraint_id="minimum-unrestricted-cash",
                kind=ConstraintKind.MIN_CASH,
                severity=ConstraintSeverity.HARD,
                description="Maintain minimum unrestricted cash",
                money_threshold=_money(policy.min_unrestricted_cash_minor, policy.currency),
                source_ref=reference,
            ),
            Constraint(
                constraint_id="minimum-30-day-liquidity",
                kind=ConstraintKind.MIN_30D_LIQUIDITY,
                severity=ConstraintSeverity.HARD,
                description="Maintain minimum projected 30-day liquidity",
                money_threshold=_money(policy.min_30d_liquidity_minor, policy.currency),
                source_ref=reference,
            ),
            Constraint(
                constraint_id="maximum-revolver-utilization",
                kind=ConstraintKind.MAX_REVOLVER_UTILIZATION,
                severity=ConstraintSeverity.HARD,
                description="Do not exceed policy revolver utilization",
                ratio_threshold=Decimal(policy.max_revolver_utilization_bps) / BPS,
                source_ref=reference,
            ),
        ]
        constraints.extend(
            Constraint(
                constraint_id=f"protected-payment-{payment_class}",
                kind=ConstraintKind.PROTECTED_PAYMENT_CLASS,
                severity=ConstraintSeverity.HARD,
                description=f"{payment_class.title()} payments cannot be deferred",
                applies_to=payment_class,
                source_ref=reference,
            )
            for payment_class in policy.protected_payment_classes.split(",")
            if payment_class
        )
        return PolicyConstraints(constraints=constraints, references=[reference], as_of=self.as_of)

    async def get_capability_manifest(self) -> CapabilityManifest:
        source_checks = {
            SourceSystem.BANK: bool(self._accounts()),
            SourceSystem.GL: self.session.exec(
                select(GLAccount).where(
                    GLAccount.tenant_id == self.tenant_id,
                    GLAccount.company_id == self.company_id,
                )
            ).first()
            is not None,
            SourceSystem.AR_LEDGER: self.session.exec(
                select(Invoice).where(
                    Invoice.tenant_id == self.tenant_id,
                    Invoice.company_id == self.company_id,
                )
            ).first()
            is not None,
            SourceSystem.AP_LEDGER: self.session.exec(
                select(VendorInvoice).where(
                    VendorInvoice.tenant_id == self.tenant_id,
                    VendorInvoice.company_id == self.company_id,
                )
            ).first()
            is not None,
            SourceSystem.DEBT: bool(self._facilities()),
            SourceSystem.DODO: self.session.exec(
                select(Subscription).where(
                    Subscription.tenant_id == self.tenant_id,
                    Subscription.company_id == self.company_id,
                )
            ).first()
            is not None,
            SourceSystem.FORECAST: self.session.exec(
                select(ForecastVersion).where(
                    ForecastVersion.tenant_id == self.tenant_id,
                    ForecastVersion.company_id == self.company_id,
                )
            ).first()
            is not None,
            SourceSystem.POLICY: self.session.exec(
                select(TreasuryPolicyRow).where(TreasuryPolicyRow.tenant_id == self.tenant_id)
            ).first()
            is not None,
        }
        available = [source for source, present in source_checks.items() if present]
        missing = [source for source, present in source_checks.items() if not present]
        return CapabilityManifest(
            available_sources=available,
            missing_sources=missing,
            notes={"tenant": self.tenant_id, "company": self.company.name},
            as_of=self.as_of,
        )

    async def get_forecast_summary(self, version_id: str | None = None) -> ForecastSummary:
        version = self._forecast_version(version_id)
        balances, lines = self._forecast_balances(version)
        policy = self._policy()
        weeks = [
            ForecastWeekRow(
                week_index=index,
                week_ending=ending,
                closing_cash=_money(amount, version.currency),
                breaches_floor=amount < policy.min_unrestricted_cash_minor,
            )
            for index, ending, amount in balances
        ]
        references = [f"forecast:{line.id}" for line in lines]
        return ForecastSummary(
            version_id=version.id,
            published=version.published,
            weeks=weeks,
            references=references,
            as_of=version.as_of.date(),
        )

    async def list_driver_assumptions(
        self, *, top_n: int = DEFAULT_TOP_N, stale_only: bool = False
    ) -> DriverAssumptions:
        version = self._forecast_version()
        assumptions = list(
            self.session.exec(
                select(Assumption).where(
                    Assumption.tenant_id == self.tenant_id,
                    Assumption.version_id == version.id,
                )
            ).all()
        )
        rows = [
            AssumptionRow(
                category=item.category,
                driver=item.key,
                value_display=f"{item.statement} ({item.basis})",
                last_refreshed=item.as_of,
                days_since_refresh=max(0, (self.as_of - item.as_of).days),
                stale=item.review_due is not None and item.review_due < self.as_of,
                reference=f"forecast:{item.id}",
            )
            for item in assumptions
        ]
        if stale_only:
            rows = [row for row in rows if row.stale]
        rows.sort(key=lambda row: (-row.days_since_refresh, row.category))
        shown, truncation = cap(rows, _limit(top_n))
        return DriverAssumptions(
            rows=shown,
            truncation=truncation,
            references=[row.reference for row in shown],
            as_of=self.as_of,
        )

    async def get_variance_bridge(
        self, *, week_ending: str | None = None, top_n: int = DEFAULT_TOP_N
    ) -> VarianceBridge:
        ending = (
            date.fromisoformat(week_ending)
            if week_ending
            else _week_start(self.as_of) - timedelta(days=1)
        )
        start = ending - timedelta(days=6)
        stored = list(
            self.session.exec(
                select(VarianceItem).where(
                    VarianceItem.tenant_id == self.tenant_id,
                    VarianceItem.week_ending == ending,
                )
            ).all()
        )
        if stored:
            rows = [
                VarianceRow(
                    category=item.category,
                    plan=_money(item.forecast_minor, item.currency),
                    actual=_money(item.actual_minor or 0, item.currency),
                    delta=_money(item.variance_minor, item.currency),
                    material=item.material,
                    reference=f"forecast:{item.id}",
                )
                for item in stored
            ]
        else:
            versions = list(
                self.session.exec(
                    select(ForecastVersion).where(
                        ForecastVersion.tenant_id == self.tenant_id,
                        ForecastVersion.company_id == self.company_id,
                        ForecastVersion.first_week_start <= start,
                        ForecastVersion.as_of <= start,
                    )
                ).all()
            )
            candidates = [
                version
                for version in versions
                if start < version.first_week_start + timedelta(weeks=version.horizon_weeks)
            ]
            if not candidates:
                raise _tool_error(f"no published forecast covers week ending {ending}")
            version = max(candidates, key=lambda item: item.as_of)
            lines = list(
                self.session.exec(
                    select(ForecastLine).where(
                        ForecastLine.tenant_id == self.tenant_id,
                        ForecastLine.version_id == version.id,
                        ForecastLine.week_start == start,
                    )
                ).all()
            )
            plan = {line.category: line.amount_minor for line in lines}
            line_ids = {line.category: line.id for line in lines}
            account_ids = {account.id for account in self._accounts()}
            transactions = list(
                self.session.exec(
                    select(BankTransaction).where(
                        BankTransaction.tenant_id == self.tenant_id,
                        BankTransaction.account_id.in_(account_ids),
                        BankTransaction.booking_date >= start,
                        BankTransaction.booking_date <= ending,
                        BankTransaction.pending.is_(False),
                    )
                ).all()
            )
            actual: dict[str, int] = defaultdict(int)
            for transaction in transactions:
                if transaction.category in CATEGORIES:
                    actual[transaction.category] += transaction.amount_minor
            policy = self._policy()
            monthly_opex = int(self.profile.section("scale")["monthly_operating_expense_minor"])
            threshold = min(
                policy.materiality_absolute_minor,
                monthly_opex * policy.materiality_pct_opex_bps // BPS,
            )
            rows = []
            for category in CATEGORIES:
                delta = actual[category] - plan.get(category, 0)
                rows.append(
                    VarianceRow(
                        category=category,
                        plan=_money(plan.get(category, 0), self.currency),
                        actual=_money(actual[category], self.currency),
                        delta=_money(delta, self.currency),
                        material=abs(delta) >= threshold,
                        reference=f"forecast:{line_ids[category]}",
                    )
                )
        total_delta = sum(row.delta.minor_units for row in rows)
        rows.sort(key=lambda row: (-abs(row.delta.minor_units), row.category))
        shown, truncation = cap(rows, _limit(top_n))
        return VarianceBridge(
            week_ending=ending,
            rows=shown,
            total_delta=_money(total_delta, self.currency),
            truncation=truncation,
            references=[row.reference for row in shown],
            as_of=self.as_of,
        )

    async def get_forecast_error_percentiles(
        self, *, horizon_weeks: int
    ) -> ForecastErrorPercentiles:
        if not 1 <= horizon_weeks <= 13:
            raise _tool_error("horizon_weeks must be between 1 and 13")
        persisted = list(
            self.session.exec(
                select(AccuracyStat).where(
                    AccuracyStat.tenant_id == self.tenant_id,
                    AccuracyStat.company_id == self.company_id,
                    AccuracyStat.horizon_weeks == horizon_weeks,
                    AccuracyStat.as_of <= self.as_of,
                )
            ).all()
        )
        if persisted:
            latest: dict[str, AccuracyStat] = {}
            for stat in persisted:
                if stat.category not in latest or stat.as_of > latest[stat.category].as_of:
                    latest[stat.category] = stat
            percentiles = [
                ErrorPercentile(
                    horizon_weeks=horizon_weeks,
                    category=stat.category,
                    p50_pct=Decimal(stat.mape_bps) / 100,
                    p90_pct=Decimal(stat.mape_bps) / 100,
                    sample_size=stat.n,
                )
                for stat in latest.values()
            ]
        else:
            version_ids = {
                version.id
                for version in self.session.exec(
                    select(ForecastVersion).where(
                        ForecastVersion.tenant_id == self.tenant_id,
                        ForecastVersion.company_id == self.company_id,
                    )
                ).all()
            }
            lines = list(
                self.session.exec(
                    select(ForecastLine).where(
                        ForecastLine.tenant_id == self.tenant_id,
                        ForecastLine.version_id.in_(version_ids),
                        ForecastLine.week_index == horizon_weeks,
                        ForecastLine.week_start < self.as_of,
                    )
                ).all()
            )
            account_ids = {account.id for account in self._accounts()}
            transactions = list(
                self.session.exec(
                    select(BankTransaction).where(
                        BankTransaction.tenant_id == self.tenant_id,
                        BankTransaction.account_id.in_(account_ids),
                        BankTransaction.pending.is_(False),
                        BankTransaction.booking_date < self.as_of,
                    )
                ).all()
            )
            actual: dict[tuple[str, date], int] = defaultdict(int)
            for transaction in transactions:
                if transaction.category in CATEGORIES:
                    actual[(transaction.category, _week_start(transaction.booking_date))] += (
                        transaction.amount_minor
                    )
            errors: dict[str, list[int]] = defaultdict(list)
            for line in lines:
                actual_amount = actual.get((line.category, line.week_start))
                if actual_amount is None:
                    continue
                denominator = abs(actual_amount) or abs(line.amount_minor)
                if denominator:
                    errors[line.category].append(
                        abs(actual_amount - line.amount_minor) * BPS // denominator
                    )
            percentiles = [
                ErrorPercentile(
                    horizon_weeks=horizon_weeks,
                    category=category,
                    p50_pct=Decimal(_nearest_rank(values, 50)) / 100,
                    p90_pct=Decimal(_nearest_rank(values, 90)) / 100,
                    sample_size=len(values),
                )
                for category, values in sorted(errors.items())
                if values
            ]
        if not percentiles:
            raise _tool_error(f"no measured forecast error at horizon {horizon_weeks}")
        return ForecastErrorPercentiles(percentiles=percentiles, as_of=self.as_of)

    def _open_invoices(self) -> list[Invoice]:
        return list(
            self.session.exec(
                select(Invoice).where(
                    Invoice.tenant_id == self.tenant_id,
                    Invoice.company_id == self.company_id,
                    Invoice.open_amount_minor > 0,
                    Invoice.status == "open",
                )
            ).all()
        )

    async def rank_collection_opportunities(
        self, *, top_n: int = DEFAULT_TOP_N
    ) -> CollectionOpportunities:
        invoices = self._open_invoices()
        customer_ids = {invoice.customer_id for invoice in invoices}
        customers = {
            customer.id: customer
            for customer in self.session.exec(
                select(Customer).where(Customer.id.in_(customer_ids))
            ).all()
        }
        curve = self.profile.collection_curve()
        rows: list[CollectionRow] = []
        for invoice in invoices:
            customer = customers[invoice.customer_id]
            days = (self.as_of - invoice.due_date).days
            bucket = aging_bucket(days)
            probability_bps = curve.probability_bps(bucket, disputed=invoice.disputed)
            expected = invoice.open_amount_minor * probability_bps // BPS
            rows.append(
                CollectionRow(
                    customer=customer.name,
                    document_ref=invoice.invoice_ref,
                    amount=_money(invoice.open_amount_minor, invoice.currency),
                    due_date=invoice.due_date,
                    days_past_due=days,
                    probability_pct=Decimal(probability_bps) / 100,
                    expected_amount=_money(expected, invoice.currency),
                    empirical_basis=(
                        f"{bucket} aging bucket; fitted collection curve"
                        + (" with dispute haircut" if invoice.disputed else "")
                    ),
                    reference=f"ar_ledger:{invoice.id}",
                )
            )
        rows.sort(key=lambda row: (-row.expected_amount.minor_units, row.due_date))
        total_open = sum(invoice.open_amount_minor for invoice in invoices)
        total_expected = sum(row.expected_amount.minor_units for row in rows)
        shown, truncation = cap(rows, _limit(top_n))
        return CollectionOpportunities(
            rows=shown,
            total_open=_money(total_open, self.currency),
            total_expected=_money(total_expected, self.currency),
            truncation=truncation,
            references=[row.reference for row in shown],
            as_of=self.as_of,
        )

    async def get_ar_aging_summary(self) -> ArAgingSummary:
        totals = {bucket: [0, 0] for bucket in AGING_BUCKETS}
        invoices = self._open_invoices()
        for invoice in invoices:
            bucket = aging_bucket((self.as_of - invoice.due_date).days)
            totals[bucket][0] += invoice.open_amount_minor
            totals[bucket][1] += 1
        buckets = [
            AgingBucket(
                label=label,
                amount=_money(totals[label][0], self.currency),
                invoice_count=totals[label][1],
            )
            for label in AGING_BUCKETS
        ]
        return ArAgingSummary(
            buckets=buckets,
            total=_money(sum(invoice.open_amount_minor for invoice in invoices), self.currency),
            references=[f"ar_ledger:{invoice.id}" for invoice in invoices],
            as_of=self.as_of,
        )

    async def rank_deferral_candidates(
        self, *, top_n: int = DEFAULT_TOP_N, max_delay_days: int = 30
    ) -> DeferralCandidates:
        if max_delay_days < 0:
            raise _tool_error("max_delay_days cannot be negative")
        invoices = list(
            self.session.exec(
                select(VendorInvoice).where(
                    VendorInvoice.tenant_id == self.tenant_id,
                    VendorInvoice.company_id == self.company_id,
                    VendorInvoice.open_amount_minor > 0,
                    VendorInvoice.status == "open",
                )
            ).all()
        )
        vendor_ids = {invoice.vendor_id for invoice in invoices}
        vendors = {
            vendor.id: vendor
            for vendor in self.session.exec(select(Vendor).where(Vendor.id.in_(vendor_ids))).all()
        }
        protected_classes = set(self._policy().protected_payment_classes.split(","))
        rows: list[DeferralRow] = []
        for invoice in invoices:
            vendor = vendors[invoice.vendor_id]
            payment_class = vendor.protected_class or "trade"
            protected = payment_class in protected_classes
            discount = (
                invoice.open_amount_minor * invoice.early_pay_discount_bps // BPS
                if invoice.early_pay_discount_bps
                else 0
            )
            rows.append(
                DeferralRow(
                    supplier=vendor.name,
                    document_ref=invoice.invoice_ref,
                    amount=_money(invoice.open_amount_minor, invoice.currency),
                    due_date=invoice.due_date,
                    max_delay_days=0 if protected else max_delay_days,
                    payment_class=payment_class,
                    protected=protected,
                    discount_forgone=(
                        _money(discount, invoice.currency)
                        if invoice.early_pay_discount_bps
                        else None
                    ),
                    reference=f"ap_ledger:{invoice.id}",
                )
            )
        rows.sort(
            key=lambda row: (
                not row.protected,
                -row.amount.minor_units,
                row.due_date,
            )
        )
        total_deferrable = sum(row.amount.minor_units for row in rows if not row.protected)
        shown, truncation = cap(rows, _limit(top_n))
        return DeferralCandidates(
            rows=shown,
            total_deferrable=_money(total_deferrable, self.currency),
            truncation=truncation,
            references=[row.reference for row in shown],
            as_of=self.as_of,
        )

    async def get_supplier_risk_profile(self, *, supplier: str) -> SupplierRiskProfile:
        vendor = self.session.exec(
            select(Vendor).where(
                Vendor.tenant_id == self.tenant_id,
                Vendor.company_id == self.company_id,
                Vendor.name == supplier,
            )
        ).first()
        if vendor is None:
            raise _tool_error(f"supplier {supplier!r} was not found")
        invoices = list(
            self.session.exec(
                select(VendorInvoice).where(
                    VendorInvoice.tenant_id == self.tenant_id,
                    VendorInvoice.company_id == self.company_id,
                )
            ).all()
        )
        supplier_invoices = [invoice for invoice in invoices if invoice.vendor_id == vendor.id]
        total_open = sum(invoice.open_amount_minor for invoice in invoices)
        supplier_open = sum(invoice.open_amount_minor for invoice in supplier_invoices)
        concentration = Decimal(supplier_open * 100) / total_open if total_open else Decimal(0)
        terms = sorted(
            (invoice.due_date - invoice.issued_date).days for invoice in supplier_invoices
        )
        contractual_terms = terms[len(terms) // 2] if terms else 0
        late = sum(
            1
            for invoice in supplier_invoices
            if invoice.open_amount_minor > 0
            and invoice.due_date < self.as_of
            and invoice.due_date >= self.as_of - timedelta(days=183)
        )
        reference = f"ap_ledger:{vendor.id}"
        return SupplierRiskProfile(
            supplier=vendor.name,
            sole_source=vendor.single_source,
            concentration_pct=concentration,
            late_payments_6m=late,
            open_disputes=0,
            contractual_terms_days=contractual_terms,
            on_credit_hold=False,
            notes=(
                f"Criticality: {vendor.criticality}; replacement lead time: "
                f"{vendor.replacement_lead_time_days or 0} days"
            ),
            references=[reference],
            as_of=self.as_of,
        )

    async def get_dodo_decline_breakdown(
        self, *, top_n: int = DEFAULT_TOP_N
    ) -> DodoDeclineBreakdown:
        subscriptions = list(
            self.session.exec(
                select(Subscription).where(
                    Subscription.tenant_id == self.tenant_id,
                    Subscription.company_id == self.company_id,
                    Subscription.status == "active",
                )
            ).all()
        )
        processor = self.profile.section("processor")
        failed_bps = BPS - int(processor["baseline_success_rate_bps"])
        soft_share_bps = int(processor["soft_decline_share_bps"])
        recovery_bps = int(processor["soft_decline_recovery_bps"])
        total_scheduled = sum(subscription.amount_minor for subscription in subscriptions)
        at_risk = total_scheduled * failed_bps // BPS
        soft_amount = at_risk * soft_share_bps // BPS
        hard_amount = at_risk - soft_amount
        soft_count = len(subscriptions) * failed_bps * soft_share_bps // BPS // BPS
        failed_count = len(subscriptions) * failed_bps // BPS
        references = [f"dodo:{subscription.id}" for subscription in subscriptions[:2]]
        while len(references) < 2:
            references.append(f"dodo:unavailable-{len(references) + 1}")
        rows = [
            DeclineRow(
                code="insufficient_funds",
                kind="soft",
                count=soft_count,
                amount=_money(soft_amount, self.currency),
                recoverable_amount=_money(soft_amount * recovery_bps // BPS, self.currency),
                retry_window_days=None,
                reference=references[0],
            ),
            DeclineRow(
                code="card_permanently_declined",
                kind="hard",
                count=max(0, failed_count - soft_count),
                amount=_money(hard_amount, self.currency),
                recoverable_amount=_money(0, self.currency),
                retry_window_days=None,
                reference=references[1],
            ),
        ]
        shown, truncation = cap(rows, _limit(top_n))
        return DodoDeclineBreakdown(
            rows=shown,
            at_risk_total=_money(at_risk, self.currency),
            recoverable_total=_money(soft_amount * recovery_bps // BPS, self.currency),
            truncation=truncation,
            references=[row.reference for row in shown],
            as_of=self.as_of,
        )

    async def resolve_evidence(self, *, reference: str) -> EvidenceRow:
        try:
            source_name, record_id, field = parse_reference(reference)
            source = SourceSystem(source_name)
        except (ValueError, TypeError):
            return EvidenceRow(
                reference=reference,
                source=SourceSystem.GL,
                excerpt="",
                resolved=False,
                as_of=self.as_of,
            )
        model_by_source = {
            SourceSystem.BANK: BankAccount,
            SourceSystem.GL: GLAccount,
            SourceSystem.AR_LEDGER: Invoice,
            SourceSystem.AP_LEDGER: VendorInvoice,
            SourceSystem.DEBT: DebtCovenant,
            SourceSystem.DODO: Subscription,
            SourceSystem.FORECAST: ForecastLine,
            SourceSystem.POLICY: TreasuryPolicyRow,
        }
        model = model_by_source.get(source)
        row = self.session.get(model, record_id) if model is not None else None
        if row is None and source is SourceSystem.AP_LEDGER:
            row = self.session.get(Vendor, record_id)
        if row is None and source is SourceSystem.FORECAST:
            row = self.session.get(Assumption, record_id)
            if row is None:
                row = self.session.get(VarianceItem, record_id)
        if row is not None and getattr(row, "tenant_id", None) != self.tenant_id:
            row = None
        if row is not None:
            row_company_id = getattr(row, "company_id", self.company_id)
            if row_company_id != self.company_id:
                row = None
        if row is None:
            return EvidenceRow(
                reference=reference,
                source=source,
                excerpt="",
                resolved=False,
                as_of=self.as_of,
            )
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
        label = (
            getattr(row, "invoice_ref", None)
            or getattr(row, "name", None)
            or getattr(row, "version_label", None)
            or record_id
        )
        return EvidenceRow(
            reference=reference,
            source=source,
            excerpt=f"{row.__class__.__name__} {label}",
            fields={field: values[field]} if field else values,
            resolved=True,
            references=[reference],
            as_of=self.as_of,
        )


__all__ = ["EngineToolset", "_money"]
