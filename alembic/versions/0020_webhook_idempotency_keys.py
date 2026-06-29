"""Add idempotency keys and event timestamps to webhook deliveries.

Adds deterministic idempotency keys for receiver-safe deduplication:
- idempotency_key: SHA256 hash of webhook_id + event + event_timestamp (unique, indexed)
- event_timestamp: Immutable timestamp when the event occurred (UTC)

This enables receivers to deduplicate webhook deliveries across retries and manual replays.

Revision ID: 0020_webhook_idempotency_keys
Revises: 0016_webhook_signature_versioning
Create Date: 2026-06-29
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime


revision = "0020_webhook_idempotency_keys"
down_revision = "0016_webhook_signature_versioning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add event_timestamp column (nullable initially for existing records)
    op.add_column(
        "webhook_deliveries",
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=True),
    )
    
    # Backfill event_timestamp from created_at for existing records
    connection = op.get_bind()
    connection.execute(
        sa.text("""
            UPDATE webhook_deliveries
            SET event_timestamp = created_at
            WHERE event_timestamp IS NULL
        """)
    )
    
    # Make event_timestamp non-nullable after backfill
    op.alter_column(
        "webhook_deliveries",
        "event_timestamp",
        nullable=False,
    )
    
    # Add idempotency_key column (nullable initially)
    op.add_column(
        "webhook_deliveries",
        sa.Column("idempotency_key", sa.String(255), nullable=True),
    )
    
    # Generate idempotency keys for existing records
    connection.execute(
        sa.text("""
            UPDATE webhook_deliveries
            SET idempotency_key = encode(digest(
                webhook_id::text || ':' || event::text || ':' || to_char(event_timestamp, 'YYYY-MM-DD"T"HH24:MI:SS.US'),
                'sha256'
            ), 'hex')
            WHERE idempotency_key IS NULL
        """)
    )
    
    # Make idempotency_key non-nullable and add unique constraint
    op.alter_column(
        "webhook_deliveries",
        "idempotency_key",
        nullable=False,
    )
    
    # Create unique index on idempotency_key
    op.create_index(
        "ix_webhook_deliveries_idempotency_key",
        "webhook_deliveries",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    # Remove unique index
    op.drop_index(
        "ix_webhook_deliveries_idempotency_key",
        table_name="webhook_deliveries",
    )
    
    # Remove columns
    op.drop_column("webhook_deliveries", "idempotency_key")
    op.drop_column("webhook_deliveries", "event_timestamp")
