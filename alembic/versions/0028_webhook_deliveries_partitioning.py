"""Partition webhook_deliveries by month on created_at.

Revision ID: 0028_webhook_deliveries_partitioning
Revises: 0027_webhook_deliveries_payload_gin
Create Date: 2026-08-27

Issue #522: ``webhook_deliveries`` grows by > 1,000,000 rows monthly,
degrading query performance. This migration converts the table to a
PostgreSQL partitioned table using RANGE partitioning on ``created_at``
with one partition per calendar month.

Notes:
  * PostgreSQL requires unique constraints on partitioned tables to include
    the partition key, so the unique index on ``idempotency_key`` becomes a
    unique index on ``(idempotency_key, created_at)``. Delivery replays
    UPDATE existing rows (they do not re-insert), so deduplication semantics
    are preserved.
  * The ORM model exposes ``dead_lettered_at`` which was never migrated into
    the old table; the partitioned table includes it so ORM writes work on
    PostgreSQL (legacy rows backfill NULL).
  * ``scripts/create_next_partition.py`` creates the following month's
    partition automatically.
"""
from datetime import datetime, timedelta

from alembic import op


revision = "0028_webhook_deliveries_partitioning"
down_revision = "0027_webhook_deliveries_payload_gin"
branch_labels = None
depends_on = None


def _month_start(year: int, month: int) -> datetime:
    return datetime(year, month, 1)


def _partition_name(start: datetime) -> str:
    return f"webhook_deliveries_{start.year:04d}_{start.month:02d}"


def _month_range(now: datetime):
    """Return (start, end) of the month containing *now*."""
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, end


_CREATE_PARENT = """
    CREATE TABLE webhook_deliveries (
        id UUID NOT NULL,
        webhook_id UUID NOT NULL,
        event webhookevent NOT NULL,
        payload JSONB NOT NULL,
        status webhookdeliverystatus NOT NULL DEFAULT 'pending',
        attempt_count INTEGER NOT NULL DEFAULT 0,
        next_retry_at TIMESTAMP WITHOUT TIME ZONE,
        response_status_code INTEGER,
        response_body TEXT,
        error_message TEXT,
        delivered_at TIMESTAMP WITHOUT TIME ZONE,
        dead_lettered_at TIMESTAMP WITHOUT TIME ZONE,
        signature_version INTEGER NOT NULL DEFAULT 1,
        idempotency_key VARCHAR(255) NOT NULL,
        event_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        PRIMARY KEY (id, created_at)
    ) PARTITION BY RANGE (created_at)
"""

_INSERT_BACKFILL = """
    INSERT INTO webhook_deliveries (
        id, webhook_id, event, payload, status, attempt_count,
        next_retry_at, response_status_code, response_body, error_message,
        delivered_at, dead_lettered_at, signature_version, idempotency_key,
        event_timestamp, created_at, updated_at
    )
    SELECT
        id, webhook_id, event, payload, status, attempt_count,
        next_retry_at, response_status_code, response_body, error_message,
        delivered_at, NULL, signature_version, idempotency_key,
        event_timestamp, created_at, updated_at
    FROM webhook_deliveries_legacy
"""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # Partitioning is PostgreSQL-specific.
        return

    op.execute("ALTER TABLE webhook_deliveries RENAME TO webhook_deliveries_legacy")
    # Index names are schema-wide: drop the legacy indexes so the same names
    # can be recreated on the partitioned parent (they are re-added below).
    op.execute("DROP INDEX IF EXISTS ix_webhook_deliveries_idempotency_key")
    op.execute("DROP INDEX IF EXISTS ix_webhook_deliveries_payload_gin")
    op.execute(_CREATE_PARENT)

    op.execute(
        "ALTER TABLE webhook_deliveries "
        "ADD CONSTRAINT webhook_deliveries_webhook_id_fkey "
        "FOREIGN KEY (webhook_id) REFERENCES webhooks (id) ON DELETE CASCADE"
    )

    # DEFAULT partition catches any row outside the explicit monthly ranges.
    op.execute(
        "CREATE TABLE webhook_deliveries_default "
        "PARTITION OF webhook_deliveries DEFAULT"
    )

    # Explicit partitions for the current and following month.
    now = datetime.utcnow()
    begin, _ = _month_range(now)
    ranges = []
    for _ in range(2):
        end = (begin.replace(day=28) + timedelta(days=4)).replace(day=1)
        ranges.append((begin, end))
        begin = end
    for begin, end in ranges:
        op.execute(
            f"CREATE TABLE {_partition_name(begin)} "
            f"PARTITION OF webhook_deliveries "
            f"FOR VALUES FROM ('{begin:%Y-%m-%d}') TO ('{end:%Y-%m-%d}')"
        )

    # Indexes on the parent propagate to existing and future partitions.
    op.execute(
        "CREATE UNIQUE INDEX ix_webhook_deliveries_idempotency_key "
        "ON webhook_deliveries (idempotency_key, created_at)"
    )
    op.execute(
        "CREATE INDEX ix_webhook_deliveries_payload_gin "
        "ON webhook_deliveries USING GIN (payload)"
    )

    op.execute(_INSERT_BACKFILL)
    op.execute("DROP TABLE webhook_deliveries_legacy")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        "ALTER TABLE webhook_deliveries RENAME TO webhook_deliveries_partitioned"
    )
    op.execute("DROP INDEX IF EXISTS ix_webhook_deliveries_idempotency_key")
    op.execute("DROP INDEX IF EXISTS ix_webhook_deliveries_payload_gin")
    op.execute(
        "CREATE TABLE webhook_deliveries ("
        "    id UUID NOT NULL,"
        "    webhook_id UUID NOT NULL,"
        "    event webhookevent NOT NULL,"
        "    payload JSONB NOT NULL,"
        "    status webhookdeliverystatus NOT NULL DEFAULT 'pending',"
        "    attempt_count INTEGER NOT NULL DEFAULT 0,"
        "    next_retry_at TIMESTAMP WITHOUT TIME ZONE,"
        "    response_status_code INTEGER,"
        "    response_body TEXT,"
        "    error_message TEXT,"
        "    delivered_at TIMESTAMP WITHOUT TIME ZONE,"
        "    dead_lettered_at TIMESTAMP WITHOUT TIME ZONE,"
        "    signature_version INTEGER NOT NULL DEFAULT 1,"
        "    idempotency_key VARCHAR(255) NOT NULL,"
        "    event_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,"
        "    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,"
        "    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,"
        "    PRIMARY KEY (id)"
        ")"
    )
    op.execute(
        "ALTER TABLE webhook_deliveries "
        "ADD CONSTRAINT webhook_deliveries_webhook_id_fkey "
        "FOREIGN KEY (webhook_id) REFERENCES webhooks (id) ON DELETE CASCADE"
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_webhook_deliveries_idempotency_key "
        "ON webhook_deliveries (idempotency_key)"
    )
    op.execute(
        "CREATE INDEX ix_webhook_deliveries_payload_gin "
        "ON webhook_deliveries USING GIN (payload)"
    )
    op.execute(_INSERT_BACKFILL.replace("webhook_deliveries_legacy", "webhook_deliveries_partitioned"))
    op.execute("DROP TABLE webhook_deliveries_partitioned")
