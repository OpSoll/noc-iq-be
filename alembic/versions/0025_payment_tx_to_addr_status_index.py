"""Add index on payment_transactions (to_address, status, created_at).

Revision ID: 0025_payment_tx_to_addr_status_index
Revises: 990e5bdf29fa
Create Date: 2026-08-27

Issue #528: searching payment settlements by wallet address and status
scans the entire transaction table without this index. The composite index
covers the (to_address, status, created_at) predicate used by the
settlement listing and reconciliation queries.
"""
from alembic import op


revision = "0025_payment_tx_to_addr_status_index"
down_revision = "990e5bdf29fa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_payment_tx_to_addr_status",
        "payment_transactions",
        ["to_address", "status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_payment_tx_to_addr_status", table_name="payment_transactions")
