"""Add immutable audit_entries table for Phase 10 controls.

Revision ID: 20260906_0003
Revises: 9fd16c017859
Create Date: 2026-09-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260906_0003"
down_revision: str | None = "9fd16c017859"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_entries",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("company_id", sa.String(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_uid", sa.String(length=160), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=512), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.CHAR(length=3), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=True),
        sa.Column("data_snapshot_ref", sa.String(length=128), nullable=True),
        sa.Column("app_version", sa.String(length=32), nullable=True),
        sa.Column("payload", sa.String(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("seq >= 1", name="ck_audit_entries_seq"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "event_uid", name="uq_audit_entries_uid"),
    )
    op.create_index("ix_audit_entries_company_id", "audit_entries", ["company_id"])
    op.create_index("ix_audit_entries_event_type", "audit_entries", ["event_type"])
    op.create_index("ix_audit_entries_recorded_at", "audit_entries", ["recorded_at"])
    op.create_index("ix_audit_entries_seq", "audit_entries", ["seq"])
    op.create_index("ix_audit_entries_tenant_id", "audit_entries", ["tenant_id"])

    if op.get_bind().dialect.name == "postgresql":
        bind = op.get_bind()
        bind.execute(sa.text("ALTER TABLE audit_entries ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text("ALTER TABLE audit_entries FORCE ROW LEVEL SECURITY"))
        bind.execute(
            sa.text("GRANT SELECT, INSERT, UPDATE, DELETE ON audit_entries TO warroom_app")
        )
        bind.execute(
            sa.text(
                """
                CREATE POLICY tenant_isolation ON audit_entries
                FOR ALL
                USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
                WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
                """
            )
        )
        # Audit log is append-only, same posture as financial_events.
        bind.execute(sa.text("REVOKE UPDATE, DELETE ON audit_entries FROM PUBLIC"))
        bind.execute(sa.text("REVOKE UPDATE, DELETE ON audit_entries FROM warroom_app"))


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        bind = op.get_bind()
        bind.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON audit_entries"))
        bind.execute(sa.text("ALTER TABLE audit_entries NO FORCE ROW LEVEL SECURITY"))
        bind.execute(sa.text("ALTER TABLE audit_entries DISABLE ROW LEVEL SECURITY"))
    op.drop_index("ix_audit_entries_tenant_id", table_name="audit_entries")
    op.drop_index("ix_audit_entries_seq", table_name="audit_entries")
    op.drop_index("ix_audit_entries_recorded_at", table_name="audit_entries")
    op.drop_index("ix_audit_entries_event_type", table_name="audit_entries")
    op.drop_index("ix_audit_entries_company_id", table_name="audit_entries")
    op.drop_table("audit_entries")
