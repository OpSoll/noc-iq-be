"""Add webhook_dead_letter_queue table.

Stores webhook deliveries that have permanently failed after exhausting all
retry attempts. Records the final HTTP response status code and error
message for audit and administrative redelivery.

Revision ID: 0030_webhook_dead_letter_queue
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0030_webhook_dead_letter_queue"
down_revision = "0029_payment_transaction_time_bounds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_dead_letter_queue",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("delivery_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("webhook_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("event", sa.String(100), nullable=False),
        sa.Column("payload", sa.Text(), nullable=True),
        sa.Column("response_status_code", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("redelivered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("redelivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_webhook_dlq_delivery_id",
        "webhook_dead_letter_queue",
        ["delivery_id"],
    )
    op.create_index(
        "ix_webhook_dlq_webhook_id",
        "webhook_dead_letter_queue",
        ["webhook_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_dlq_webhook_id")
    op.drop_index("ix_webhook_dlq_delivery_id")
    op.drop_table("webhook_dead_letter_queue")
