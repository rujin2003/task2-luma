"""Row-level security for every tenant-scoped table.

`tenant_id` on a column is not isolation; it is a hint. Three details decide
whether RLS actually holds, and all three are easy to omit:

* `FORCE ROW LEVEL SECURITY` — without it the table owner (which is usually the
  migration role, and often the application role too) bypasses every policy.
* The application connects as a role that is neither superuser nor `BYPASSRLS`.
* The policy reads the tenant from a session GUC set per connection, so a
  missing GUC denies rather than reveals.

PostgreSQL only. SQLite has no RLS, so this is a no-op there and the equivalent
guarantee is covered by the `no_cross_tenant_references` invariant.

Revision ID: 20260906_0002
Revises: 51d445fe83f6
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260906_0002"
down_revision: str | None = "51d445fe83f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "warroom_app"

TENANT_TABLES: tuple[str, ...] = (
    "accounting_periods",
    "accuracy_stats",
    "agent_runs",
    "approval_routes",
    "approvals",
    "assumptions",
    "bank_accounts",
    "bank_reconciliation_items",
    "bank_reconciliations",
    "bank_transactions",
    "calendar_events",
    "companies",
    "customers",
    "debt_covenants",
    "debt_facilities",
    "evidence",
    "financial_events",
    "forecast_lines",
    "forecast_versions",
    "gl_accounts",
    "gl_transactions",
    "invoices",
    "journal_entries",
    "overrides",
    "payment_applications",
    "payment_runs",
    "payments",
    "recommendations",
    "scenarios",
    "stress_tests",
    "subscriptions",
    "treasury_policies",
    "variance_items",
    "vendor_invoices",
    "vendors",
    "worklist_items",
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    bind = op.get_bind()
    bind.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                    CREATE ROLE {APP_ROLE} NOLOGIN NOBYPASSRLS;
                END IF;
            END
            $$;
            """
        )
    )
    for table in TENANT_TABLES:
        bind.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}"))
        # A missing or empty app.tenant_id yields no rows rather than all rows.
        bind.execute(
            sa.text(
                f"""
                CREATE POLICY tenant_isolation ON {table}
                FOR ALL
                USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
                WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
                """
            )
        )

    # The event ledger is append-only: no role may rewrite history.
    bind.execute(sa.text("REVOKE UPDATE, DELETE ON financial_events FROM PUBLIC"))
    bind.execute(sa.text(f"REVOKE UPDATE, DELETE ON financial_events FROM {APP_ROLE}"))


def downgrade() -> None:
    if not _is_postgres():
        return
    bind = op.get_bind()
    for table in TENANT_TABLES:
        bind.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
        bind.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
