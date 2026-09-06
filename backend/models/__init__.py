"""SQLModel entities. Alembic and tests import metadata from here.

Conventions live in `backend.models.base`; closed vocabularies in
`backend.models.vocab`; the reconciliation gate in `backend.models.invariants`.
"""

from sqlmodel import SQLModel

from backend.models.ap import PaymentRun, Vendor, VendorInvoice
from backend.models.ar import Customer, Invoice, Payment, PaymentApplication
from backend.models.audit import AuditEntry
from backend.models.banking import (
    BankAccount,
    BankReconciliation,
    BankReconciliationItem,
    BankTransaction,
)
from backend.models.base import Bitemporal, Sourced, TenantOwned, derived_id, new_id
from backend.models.calendar import AccountingPeriod, BankHoliday, CalendarEvent
from backend.models.company import Company
from backend.models.debt import DebtCovenant, DebtFacility
from backend.models.events import EventLog, FinancialEvent
from backend.models.forecast import (
    AccuracyStat,
    Assumption,
    ForecastLine,
    ForecastVersion,
    Override,
    VarianceItem,
)
from backend.models.gl import (
    CANONICAL_ROLES,
    CASH_ROLES,
    GLAccount,
    GLTransaction,
    JournalEntry,
    UnbalancedJournal,
    assert_balanced,
)
from backend.models.policy import TreasuryPolicyRow
from backend.models.revenue import Subscription
from backend.models.workflow import (
    AgentRun,
    Approval,
    ApprovalRoute,
    EvidenceRow,
    Recommendation,
    Scenario,
    StressTest,
    WorklistItem,
)

metadata = SQLModel.metadata

__all__ = [
    "CANONICAL_ROLES",
    "CASH_ROLES",
    "AccountingPeriod",
    "AccuracyStat",
    "AgentRun",
    "Approval",
    "ApprovalRoute",
    "Assumption",
    "AuditEntry",
    "BankAccount",
    "BankHoliday",
    "BankReconciliation",
    "BankReconciliationItem",
    "BankTransaction",
    "Bitemporal",
    "CalendarEvent",
    "Company",
    "Customer",
    "DebtCovenant",
    "DebtFacility",
    "EventLog",
    "EvidenceRow",
    "FinancialEvent",
    "ForecastLine",
    "ForecastVersion",
    "GLAccount",
    "GLTransaction",
    "Invoice",
    "JournalEntry",
    "Override",
    "Payment",
    "PaymentApplication",
    "PaymentRun",
    "Recommendation",
    "SQLModel",
    "Scenario",
    "Sourced",
    "StressTest",
    "Subscription",
    "TenantOwned",
    "TreasuryPolicyRow",
    "UnbalancedJournal",
    "VarianceItem",
    "Vendor",
    "VendorInvoice",
    "WorklistItem",
    "assert_balanced",
    "derived_id",
    "metadata",
    "new_id",
]
