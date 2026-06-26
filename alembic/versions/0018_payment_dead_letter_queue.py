from alembic import op
import sqlalchemy as sa


revision = "0018_payment_dead_letter_queue"
down_revision = "0016_webhook_signature_versioning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_transactions",
        sa.Column("dead_letter_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "payment_transactions",
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payment_transactions", "dead_lettered_at")
    op.drop_column("payment_transactions", "dead_letter_reason")
