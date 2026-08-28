"""Add transaction timeout bounds to payment_transactions.

Enforces a maximum 300-second (5 minute) time window for unsubmitted payment
transactions. Adds time_bounds_min/max columns and fee_re_estimation_pending
flag to support automatic expiry and re-queueing.

Revision ID: 0029_payment_transaction_time_bounds
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0029_payment_transaction_time_bounds"
down_revision = "990e5bdf29fa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_transactions",
        sa.Column("time_bounds_min", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "payment_transactions",
        sa.Column("time_bounds_max", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "payment_transactions",
        sa.Column("fee_re_estimation_pending", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "payment_transactions",
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payment_transactions", "expired_at")
    op.drop_column("payment_transactions", "fee_re_estimation_pending")
    op.drop_column("payment_transactions", "time_bounds_max")
    op.drop_column("payment_transactions", "time_bounds_min")
