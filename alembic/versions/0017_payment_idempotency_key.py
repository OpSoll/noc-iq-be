from alembic import op
import sqlalchemy as sa


revision = "0017_payment_idempotency_key"
down_revision = "0016_webhook_signature_versioning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_transactions",
        sa.Column("idempotency_key", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_payment_transactions_idempotency_key",
        "payment_transactions",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_payment_transactions_idempotency_key")
    op.drop_column("payment_transactions", "idempotency_key")
