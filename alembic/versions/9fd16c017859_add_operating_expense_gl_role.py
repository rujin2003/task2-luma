"""Add the operating_expense canonical GL role.

Every non-financing cash outflow needs a P&L counterpart, or the ledger cannot
balance: paying rent debits *something*, and the only alternatives in the
original role list were balance-sheet roles that would have been silently
corrupted instead. The seed generator's postings are the first consumer.

Autogenerate does not detect CHECK-constraint changes, so this migration is
written by hand and recreates the constraint on both dialects.

Revision ID: 9fd16c017859
Revises: 20260906_0002
Create Date: 2026-09-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9fd16c017859"
down_revision: str | None = "20260906_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_gl_accounts_role"

_ROLES_BEFORE = (
    "cash_operating",
    "cash_restricted",
    "cash_mmf",
    "ar_trade",
    "ar_intercompany",
    "ar_other",
    "deferred_revenue",
    "ap_trade",
    "accrued_payroll",
    "taxes_payable",
    "accrued_expenses",
    "revolver_drawn",
    "term_debt",
    "interest_expense",
    "fx_gain_loss",
    "revenue",
    "opening_equity",
)

_ROLES_AFTER = (
    *_ROLES_BEFORE[:-1],
    "operating_expense",
    "opening_equity",
)


def _condition(roles: Sequence[str]) -> str:
    rendered = ", ".join(f"'{role}'" for role in roles)
    return f"role IN ({rendered})"


def _replace(condition: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite cannot alter a constraint in place; batch mode rebuilds the
        # table with the new definition.
        with op.batch_alter_table("gl_accounts", schema=None) as batch_op:
            batch_op.drop_constraint(CONSTRAINT, type_="check")
            batch_op.create_check_constraint(CONSTRAINT, sa.text(condition))
        return
    op.drop_constraint(CONSTRAINT, "gl_accounts", type_="check")
    op.create_check_constraint(CONSTRAINT, "gl_accounts", sa.text(condition))


def upgrade() -> None:
    _replace(_condition(_ROLES_AFTER))


def downgrade() -> None:
    # Rows already using the new role would violate the narrower constraint, so
    # they are retired first rather than left to fail the DDL.
    op.execute(
        sa.text("UPDATE gl_accounts SET role = 'accrued_expenses' WHERE role = 'operating_expense'")
    )
    _replace(_condition(_ROLES_BEFORE))
