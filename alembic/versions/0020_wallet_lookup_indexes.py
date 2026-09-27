"""Add wallet lookup indexes for stellar_wallet and payment address columns (BE-W5-028).

Adds performance indexes for:
- users.stellar_wallet: direct wallet lookups by Stellar public key
- payment_transactions.from_address: sender-side payment queries
- payment_transactions.to_address: recipient-side payment queries

Revision ID: 0020_wallet_lookup_indexes
Revises: 0019_payment_failure_taxonomy
Create Date: 2026-06-29
"""
from alembic import op
import sqlalchemy as sa


revision = "0020_wallet_lookup_indexes"
down_revision = "0019_payment_failure_taxonomy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_users_stellar_wallet",
        "users",
        ["stellar_wallet"],
        postgresql_where=sa.text("stellar_wallet IS NOT NULL"),
    )
    op.create_index(
        "ix_payment_transactions_from_address",
        "payment_transactions",
        ["from_address"],
    )
    op.create_index(
        "ix_payment_transactions_to_address",
        "payment_transactions",
        ["to_address"],
    )


def downgrade() -> None:
    op.drop_index("ix_payment_transactions_to_address", table_name="payment_transactions")
    op.drop_index("ix_payment_transactions_from_address", table_name="payment_transactions")
    op.drop_index("ix_users_stellar_wallet", table_name="users")
