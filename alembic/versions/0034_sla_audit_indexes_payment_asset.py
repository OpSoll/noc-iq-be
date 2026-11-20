"""index sla_results.created_at, audit_logs.actor_id; non-null payment asset_code

Revision ID: 0034_sla_audit_indexes_payment_asset
Revises: 0033_outages_assigned_to_index
Create Date: 2026-11-20

"""
import sqlalchemy as sa
from alembic import op

revision = "0034_sla_audit_indexes_payment_asset"
down_revision = "0033_outages_assigned_to_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_sla_results_created_at", "sla_results", ["created_at"], if_not_exists=True)
    op.create_index("ix_audit_logs_actor", "audit_logs", ["actor_id"], if_not_exists=True)
    op.execute("UPDATE payment_transactions SET asset_code = 'XLM' WHERE asset_code IS NULL")
    op.alter_column("payment_transactions", "asset_code", existing_type=sa.String(20), nullable=False)


def downgrade() -> None:
    op.alter_column("payment_transactions", "asset_code", existing_type=sa.String(20), nullable=True)
    op.drop_index("ix_audit_logs_actor", table_name="audit_logs", if_exists=True)
    op.drop_index("ix_sla_results_created_at", table_name="sla_results", if_exists=True)
