from alembic import op
import sqlalchemy as sa


revision = "0019_payment_failure_taxonomy"
down_revision = "0016_webhook_signature_versioning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_transactions",
        sa.Column("failure_taxonomy", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payment_transactions", "failure_taxonomy")
