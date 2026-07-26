"""Add grace-window columns for webhook secret rotation (BE-295).

Revision ID: 0021_webhook_rotation_grace_window
Revises: 0020_webhook_idempotency_keys
Create Date: 2026-06-29
"""
from alembic import op
import sqlalchemy as sa


revision = "0021_webhook_rotation_grace_window"
down_revision = "0020_webhook_idempotency_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "webhooks",
        sa.Column("previous_secret", sa.String(255), nullable=True),
    )
    op.add_column(
        "webhooks",
        sa.Column("rotation_grace_expires_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("webhooks", "rotation_grace_expires_at")
    op.drop_column("webhooks", "previous_secret")
