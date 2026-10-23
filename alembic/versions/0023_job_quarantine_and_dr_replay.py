"""BE-W5-045 / BE-W5-054: poison-message quarantine + DR replay support

Revision ID: 0023_job_quarantine_and_dr_replay
Revises: 0022_webhook_events_gin_index
Create Date: 2026-07-28

Adds:
  * ``payload_hash`` (sha256 of canonicalised payload, indexed)
  * ``quarantine_reason``
  * ``quarantined_at``
  * Extends the ``jobstatus`` PostgreSQL enum with the ``QUARANTINED`` value
    so existing rows are not affected while new transitions are valid.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0023_job_quarantine_and_dr_replay"
down_revision = "0022_webhook_events_gin_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new quarantine columns to jobs table.
    op.add_column(
        "jobs",
        sa.Column("payload_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("quarantine_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("quarantined_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "idx_jobs_payload_hash",
        "jobs",
        ["payload_hash"],
        unique=False,
    )

    # Extend the jobstatus enum with the QUARANTINED value.
    # Postgres requires ALTER TYPE ... ADD VALUE, which cannot run inside a
    # transaction block (ActiveSqlTransaction); the autocommit_block wrapper
    # commits the DDL statement on its own (Issue #518). IF NOT EXISTS makes
    # the statement idempotent on PG ≥ 14.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'quarantined'"
        )


def downgrade() -> None:
    op.drop_index("idx_jobs_payload_hash", table_name="jobs")
    op.drop_column("jobs", "quarantined_at")
    op.drop_column("jobs", "quarantine_reason")
    op.drop_column("jobs", "payload_hash")
    # Removing enum values is not reversible in Postgres; leave value in place.
