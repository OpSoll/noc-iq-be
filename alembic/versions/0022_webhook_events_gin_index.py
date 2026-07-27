"""Add GIN index on webhooks.events for fast JSON containment queries (BE-W5-XXX).

Revision ID: 0022_webhook_events_gin_index
Revises: 0021_webhook_rotation_grace_window
Create Date: 2026-06-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0022_webhook_events_gin_index"
down_revision = "0021_webhook_rotation_grace_window"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create GIN index on events column for JSON containment queries
    # This enables efficient @> operator lookups like: events @> '["sla.violation"]'
    op.execute(
        "CREATE INDEX idx_webhooks_events_gin ON webhooks USING GIN (events)"
    )


def downgrade() -> None:
    op.drop_index("idx_webhooks_events_gin", table_name="webhooks")
