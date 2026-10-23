"""Add payment_dead_letter_queue table (issue #561)

Revision ID: 0032_payment_dead_letter_queue
Revises: f86d8db3947f
Create Date: 2026-08-29

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0032_payment_dead_letter_queue"
down_revision: Union[str, None] = "f86d8db3947f"
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payment_dead_letter_queue",
        sa.Column("id", sa.String(), primary_key=True, index=True),
        sa.Column("original_payment_id", sa.String(), nullable=False, index=True),
        sa.Column("transaction_hash", sa.String(255), nullable=True),
        sa.Column("from_address", sa.String(255), nullable=False),
        sa.Column("to_address", sa.String(255), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("asset_code", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_description", sa.Text(), nullable=True),
        sa.Column("retry_class", sa.String(50), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, default=0),
        sa.Column("resubmitted", sa.Boolean(), nullable=False, default=False),
        sa.Column("resubmitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("outage_id", sa.String(), nullable=True),
        sa.Column("sla_result_id", sa.Integer(), nullable=True),
        sa.Column("raw_horizon_response", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_payment_dlq_created_at",
        "payment_dead_letter_queue",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_payment_dlq_created_at", table_name="payment_dead_letter_queue")
    op.drop_table("payment_dead_letter_queue")
